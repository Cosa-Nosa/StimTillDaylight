"""
DBD Tracker — main entry. Registry-driven.

All event behavior lives in events.yaml. This file just:
  - sets up the GUI / buttplug / asyncio runtime
  - calls tracker.refresh() each frame
  - reads rising/falling/state flags and applies the EventSpec's vibe rules
  - hands the same flags to stats_manager.update_from_tracker() for CSV stats
"""

import configparser
import logging
import asyncio
import json
import time
import sys
import os
import re

from buttplug import Client, WebsocketConnector, ProtocolSpec
from pynput import keyboard
import psutil as ps

try:
    import FreeSimpleGUI as sg
except ImportError:
    import PySimpleGUI as sg

from event_registry import load_event_registry
from dbdstate import DBDStateTracker
from vibe_manager import VibeManager, clamp_value
from stats_manager import StatsManager
from csv_logger import CSVLogger


def resource_path(rel):
    return os.path.join(os.path.abspath("."), rel)


def kill_other_tracker_instances():
    current_pid = os.getpid()
    for p in ps.process_iter():
        try:
            if re.search(r"DBDTracker_v\d+\.\d+\.\d+\.exe$", p.name()):
                if p.pid != current_pid:
                    p.terminate()
        except (ps.NoSuchProcess, ps.AccessDenied):
            continue


# ── Config ──────────────────────────────────────────────────────────────────────

config = configparser.ConfigParser()
config.read(resource_path("config.ini"))
config_fault = [False, ""]

try:
    BEEP_ENABLED                          = config["DBDTracker"].getboolean("BEEP_ENABLED")
    USING_INTIFACE                        = config["DBDTracker"].getboolean("USING_INTIFACE")
    MAX_VIBE_INTENSITY                    = clamp_value(config["DBDTracker"].getfloat("MAX_VIBE_INTENSITY"), 1, value_name="MAX_VIBE_INTENSITY")
    SCALE_ALL_INTENSITIES_BY_MAX_INTENSITY = config["DBDTracker"].getboolean("SCALE_ALL_INTENSITIES_BY_MAX_INTENSITY")
    EXCLUDED_DEVICE_NAMES                 = json.loads(config["DBDTracker"].get("EXCLUDED_DEVICE_NAMES"))
    EMERGENCY_STOP_KEY_COMBO              = keyboard.HotKey.parse(config["DBDTracker"]["EMERGENCY_STOP_KEY_COMBO"])
    EVENTS_YAML_PATH                      = config["DBDTracker"]["EVENTS_YAML_PATH"]
    TEMPLATES_DIR                         = config["DBDTracker"]["TEMPLATES_DIR"]
    BLEEDING_OUT_EVENT                    = config["DBDTracker"].getint("BLEEDING_OUT_EVENT")
    BLEEDING_OUT_PATTERN                  = json.loads(config["DBDTracker"].get("BLEEDING_OUT_PATTERN"))
except Exception as err:
    config_fault[0] = True
    config_fault[1] = err


window = sg.Window("DBDTracker")
client = Client("DBDTracker", ProtocolSpec.v3) if USING_INTIFACE else None
vibe_manager = None


def get_devices():
    if client is None:
        return []
    return [d for d in client.devices.values() if d.name not in EXCLUDED_DEVICE_NAMES]


def update_device_count(last):
    current = len(get_devices())
    if current != last:
        window["-DEVICE_COUNT-"].update(current)
    return current


def for_canonical(f):
    return lambda k: f(emergency_stop_listener.canonical(k))


def emergency_stop():
    if vibe_manager is not None:
        vibe_manager.stopped = True


# ── Inner loop ──────────────────────────────────────────────────────────────────

async def run_dbd_tracker():
    try:
        MAX_REFRESH_RATE     = config["DBDTracker"].getint("MAX_REFRESH_RATE")
        DEAD_REFRESH_RATE    = config["DBDTracker"].getfloat("DEAD_REFRESH_RATE")
        CSV_OUTPUT_PATH      = config["DBDTracker"]["CSV_OUTPUT_PATH"]
        MATCH_END_COOLDOWN_S = config["DBDTracker"].getfloat("MATCH_END_COOLDOWN_S")
    except Exception as cfg_err:
        config_fault[0] = True
        config_fault[1] = cfg_err

    if not config_fault[0] and window["-PROGRAM_STATUS-"].get() != "INTIFACE ERROR":
        registry = load_event_registry(EVENTS_YAML_PATH, TEMPLATES_DIR)
        if not registry:
            print("[DBDTracker] WARNING: no templates loaded. "
                  "Drop t_<name>.png files into data/templates/ and edit events.yaml.")
        tracker = DBDStateTracker(registry)
        stats_manager = StatsManager(registry)
        csv_logger = CSVLogger(CSV_OUTPUT_PATH)
        match_end_cooldown_until = 0.0
        device_count = 0

    while True:
        await asyncio.sleep(0)

        if config_fault[0]:
            window["Start"].update(disabled=True)
            window["-PROGRAM_STATUS-"].update("CONFIG ERROR")
            print(f"Error reading config: {config_fault[1]}")
            event, values = window.read()
            if event == sg.WIN_CLOSED or event == "Quit":
                window.close(); print("Window closed."); break

        if USING_INTIFACE and (client is None or not client.connected):
            if window["-PROGRAM_STATUS-"].get() != "INTIFACE ERROR":
                window["Start"].update(disabled=True)
                window["-PROGRAM_STATUS-"].update("INTIFACE ERROR")
                print("Lost connection to Intiface. Make sure Intiface Central is started and restart DBDTracker.")
                event, values = window.read()
                if event == sg.WIN_CLOSED or event == "Quit":
                    window.close(); print("Window closed."); break

        device_count = update_device_count(device_count)
        event, values = window.read(timeout=10)

        if event == sg.WIN_CLOSED or event == "Quit":
            window.close(); print("Window closed."); break

        elif event == "Start":
            window["Stop"].update(disabled=False)
            window["Start"].update(disabled=True)
            window["-PROGRAM_STATUS-"].update("RUNNING")
            print("Running...")
            vibe_manager.stopped = False
            tracker.start_capturing(MAX_REFRESH_RATE)
            counter = 0
            start_time = time.time()
            last_refresh = 0

            while True:
                await asyncio.sleep(0)
                if USING_INTIFACE and not client.connected:
                    break

                counter += 1
                device_count = update_device_count(device_count)
                current_time = time.time()
                await vibe_manager.update(current_time)
                event, values = window.read(timeout=1)

                if vibe_manager.stopped:
                    print("Emergency stop detected.")
                    event = "Stop"

                if event == sg.WIN_CLOSED or event == "Quit":
                    window.close(); break
                elif event == "Stop":
                    await vibe_manager.stop_all_devices()
                    window["-PROGRAM_STATUS-"].update("STOPPING")
                    window["Stop"].update(disabled=True)
                    window["Quit"].update(disabled=True)
                    print("Stopped.")
                    window.refresh()
                    break

                # Throttle while out of match
                active_rate = MAX_REFRESH_RATE if stats_manager.in_match else DEAD_REFRESH_RATE
                if current_time < last_refresh + (1 / float(active_rate)):
                    continue
                last_refresh = current_time

                try:
                    tracker.refresh()
                except Exception as ex:
                    print(f"refresh() error: {ex}")
                    continue

                # ── Match boundary management ─────────────────────────────
                if not stats_manager.in_match and current_time > match_end_cooldown_until:
                    # match_start lifecycle event drives this
                    if any(spec.type == "lifecycle" and spec.action == "start_match" and tracker.rising[name]
                           for name, spec in registry.items()):
                        tracker.reset_for_new_match()
                        stats_manager.start_match()

                # Per-frame stats update
                if stats_manager.in_match:
                    stats_manager.update_from_tracker(tracker)

                # ── Bleeding-out override (analog of HACKED_EVENT) ────────
                bleeding_out_active = tracker.state.get("bleeding_out", False)
                bleeding_vibe_active = vibe_manager.vibe_exists_for_trigger("bleeding_out")
                if BLEEDING_OUT_EVENT != 0:
                    if bleeding_out_active and not bleeding_vibe_active:
                        if BLEEDING_OUT_EVENT == 1:
                            vibe_manager.clear_vibes()
                            vibe_manager.add_permanent_vibe(0, "bleeding_out")
                        elif BLEEDING_OUT_EVENT == 2:
                            vibe_manager.clear_vibes()
                            vibe_manager.add_permanent_pattern(BLEEDING_OUT_PATTERN, "bleeding_out")
                    elif not bleeding_out_active and bleeding_vibe_active:
                        vibe_manager.remove_vibe_by_trigger("bleeding_out")

                # ── Apply per-event vibe rules from the registry ──────────
                if not bleeding_vibe_active:
                    apply_registry_vibes(tracker, registry, vibe_manager)

                # ── Match end detection ───────────────────────────────────
                end_now = False
                end_outcome = tracker.outcome
                for name, spec in registry.items():
                    if spec.type == "lifecycle" and spec.action == "end_match" and tracker.rising[name]:
                        end_now = True
                    if spec.type == "outcome" and tracker.rising[name] and spec.triggers_end_match:
                        end_now = True
                        end_outcome = spec.outcome

                if stats_manager.in_match and end_now:
                    record = stats_manager.end_match(outcome=end_outcome)
                    if record:
                        csv_logger.write(record)
                    tracker.reset_for_match_end()
                    match_end_cooldown_until = current_time + MATCH_END_COOLDOWN_S
                    # Keep any timed result vibes (like escape); clear continuous toggles
                    vibe_manager.clear_vibes_matching_regex(
                        r"^(in_chase|repairing|healing_self|healing_other|injured|on_hook|unhooking)$"
                    )

                if event == sg.WIN_CLOSED or event == "Quit":
                    print("Window closed."); break

            duration = time.time() - start_time
            if counter > 0:
                print(f"Loops: {counter} | LPS: {round(counter / duration, 2)} | "
                      f"Avg: {round(1000 * (duration / counter), 2)}ms")
            window.refresh()
            tracker.stop_capturing()

            if stats_manager.in_match:
                record = stats_manager.end_match(outcome=tracker.outcome or "interrupted")
                if record:
                    csv_logger.write(record)

            window["-PROGRAM_STATUS-"].update("READY")
            window["Quit"].update(disabled=False)
            window["Start"].update(disabled=False)


def apply_registry_vibes(tracker, registry, vibe_manager):
    """
    The entire vibe wiring, driven by EventSpec metadata.
    Counter events → add_timed_vibe on rising edge.
    Continuous events → toggle_vibe_to_condition with the current state.
    Continuous events with on_complete → add_timed_vibe on falling edge.
    Outcome events → add_timed_vibe on rising edge (once per match).
    """
    for name, spec in registry.items():
        if spec.type == "counter":
            if tracker.rising[name] and spec.vibe:
                vibe_manager.add_timed_vibe(
                    spec.vibe.intensity, spec.vibe.trigger, spec.vibe.duration
                )

        elif spec.type == "continuous":
            if spec.vibe:
                vibe_manager.toggle_vibe_to_condition(
                    spec.vibe.trigger, spec.vibe.intensity, tracker.state[name]
                )
            if spec.on_complete and tracker.falling[name] and spec.on_complete.vibe:
                v = spec.on_complete.vibe
                vibe_manager.add_timed_vibe(v.intensity, v.trigger, v.duration)

        elif spec.type == "outcome":
            if tracker.rising[name] and spec.vibe:
                if not vibe_manager.vibe_for_trigger_created_within_seconds(
                    spec.vibe.trigger, max(spec.vibe.duration * 2, 10.0)
                ):
                    vibe_manager.add_timed_vibe(
                        spec.vibe.intensity, spec.vibe.trigger, spec.vibe.duration
                    )
        elif spec.type == "digit_state":
            if (tracker.digit_state_decremented[name]
                    and spec.on_decrement
                    and spec.on_decrement.vibe):
                v = spec.on_decrement.vibe
                vibe_manager.add_timed_vibe(v.intensity, v.trigger, v.duration)


# ── Outer main ──────────────────────────────────────────────────────────────────

async def main():
    global window, vibe_manager

    OUTPUT_WINDOW_ENABLED = True
    CONTINUOUS_SCANNING   = False
    WEBSOCKET_ADDRESS     = "ws://127.0.0.1"
    WEBSOCKET_PORT        = "12345"
    try:
        OUTPUT_WINDOW_ENABLED = config["DBDTracker"].getboolean("OUTPUT_WINDOW_ENABLED")
        CONTINUOUS_SCANNING   = config["DBDTracker"].getboolean("CONTINUOUS_SCANNING")
        WEBSOCKET_ADDRESS     = config["DBDTracker"]["WEBSOCKET_ADDRESS"]
        WEBSOCKET_PORT        = config["DBDTracker"]["WEBSOCKET_PORT"]
    except Exception as cfg_err:
        config_fault[0] = True
        config_fault[1] = cfg_err

    scanning = False

    layout = [
        [sg.Text("Devices connected:"), sg.Text("0", size=(4, 1), key="-DEVICE_COUNT-")],
        [sg.Text("Current intensity:"), sg.Text("0%", size=(17, 1), key="-CURRENT_INTENSITY-")],
        [sg.Text("Program status:"),    sg.Text("READY", size=(15, 1), key="-PROGRAM_STATUS-")],
        [sg.Button("Start"), sg.Button("Stop", disabled=True), sg.Button("Quit")],
    ]
    if OUTPUT_WINDOW_ENABLED:
        layout.insert(0, [sg.Multiline(size=(60, 15), disabled=True, reroute_stdout=True, autoscroll=True)])

    window = sg.Window("DBDTracker", layout, finalize=True)

    def on_intensity_change(current, real):
        try:
            if current == real:
                window["-CURRENT_INTENSITY-"].update(f"{int(current * 100)}%")
            else:
                window["-CURRENT_INTENSITY-"].update(f"{int(current * 100)}% (max {int(MAX_VIBE_INTENSITY * 100)}%)")
        except Exception:
            pass

    vibe_manager = VibeManager(
        get_devices=get_devices,
        max_vibe_intensity=MAX_VIBE_INTENSITY,
        scale_by_max=SCALE_ALL_INTENSITIES_BY_MAX_INTENSITY,
        beep_enabled=BEEP_ENABLED,
        on_intensity_change=on_intensity_change,
    )

    print("Ensure you read READ_BEFORE_USING.txt before using this program.\n-")
    if not config_fault[0]:
        emergency_stop_listener.start()

    if USING_INTIFACE:
        connector = WebsocketConnector(f"{WEBSOCKET_ADDRESS}:{WEBSOCKET_PORT}", logger=client.logger)
        try:
            await client.connect(connector)
            print("Connected to Intiface")
        except Exception as ex:
            print(ex)
            print("Make sure Intiface server is running, then restart DBDTracker.")
            window["-PROGRAM_STATUS-"].update("INTIFACE ERROR")
            window["Start"].update(disabled=True)

        try:
            if client.connected:
                await client.start_scanning()
                scanning = True
                if not CONTINUOUS_SCANNING:
                    await asyncio.sleep(0.2)
                    await client.stop_scanning()
                    scanning = False
                print("Started scanning")
        except Exception as ex:
            print(f"Could not initiate scanning: {ex}")
            window["Start"].update(disabled=True)

    task = asyncio.create_task(run_dbd_tracker())
    try:
        await task
    except Exception as ex:
        await vibe_manager.stop_all_devices()
        window["-PROGRAM_STATUS-"].update("CRITICAL ERROR")
        print(f"CRITICAL ERROR: {ex}")
        if BEEP_ENABLED:
            try:
                import winsound; winsound.Beep(1000, 500)
            except Exception:
                pass
        event, values = window.read()
        if event == sg.WIN_CLOSED or event == "Quit":
            window["Stop"].update(disabled=True); window["Quit"].update(disabled=True); window.close()

    try:
        emergency_stop_listener.stop()
    except Exception:
        pass

    await vibe_manager.stop_all_devices()
    if not config_fault[0]:
        if USING_INTIFACE and client and client.connected:
            if scanning:
                await client.stop_scanning()
            await client.disconnect()
            print("Disconnected.")
    window.close()
    print("Quitting.")


# ── Bootstrap ───────────────────────────────────────────────────────────────────

kill_other_tracker_instances()

if not config_fault[0]:
    hotkey = keyboard.HotKey(EMERGENCY_STOP_KEY_COMBO, emergency_stop)
    emergency_stop_listener = keyboard.Listener(
        on_press=for_canonical(hotkey.press),
        on_release=for_canonical(hotkey.release),
    )

sg.theme("DarkAmber")
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
asyncio.run(main(), debug=False)
