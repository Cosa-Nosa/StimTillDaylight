"""
vibe_manager.py — Vibe class hierarchy and VibeManager.

Game-agnostic. Translates triggers → actuator commands. Direct port of
OverStim's VibeManager with the GUI hook factored out into an injected
callback and device accessor.
"""

import re


def clamp_value(value, max_value, min_value=0, value_name="value"):
    # Check if value exceeds maximum allowed
    if value > max_value:
        # Clamp to max
        value = max_value
    # Check if value is below minimum allowed
    elif value < min_value:
        # Warn user and clamp to min
        print(f"Tried to set {value_name} to {value} but it cannot be lower than {min_value}. Setting it to {min_value}.")
        value = min_value
    # Return clamped value
    return value


def round_value_to_nearest_step(value, step):
    # Calculate decimal places in the step size
    digits = len(str(float(step)).split(".")[1])
    # Round value to nearest step increment with appropriate precision
    return round(step * round(value / step, 0), digits)


# ── Vibes ─────────────────────────────────────────────────────────────────

class Vibe:
    def __init__(self, pattern, trigger, current_time, loop_count=None, total_duration=None):
        # Convert pattern pairs [intensity, duration] to dict format with Intensity and Expiry keys
        self.pattern_template = [{"Intensity": p[0], "Expiry": p[1]} for p in pattern]
        # Build cumulative expiry times for the pattern
        self.pattern = self._build_pattern(current_time)
        # Store the trigger name (e.g., "gen_complete", "on_hook")
        self.trigger = trigger
        # Index tracks current position in pattern playback
        self.current_index = 0
        # Record when this vibe was created
        self.creation_time = current_time
        # Set expiry based on duration, loop count, or default fallback
        if total_duration:
            # Timed vibes expire after specified duration
            self.expiry = current_time + total_duration
        elif loop_count:
            # Looped vibes expire after pattern repeats N times
            self.expiry = current_time + ((self.pattern[-1]["Expiry"] - current_time) * loop_count)
        else:
            # Permanent vibes expire at far future (double current time)
            self.expiry = current_time * 2

    def _build_pattern(self, current_time):
        # Initialize empty pattern list with absolute expiry times
        pattern = []
        # Start accumulating from current time
        expiry = current_time
        # Iterate through template pairs
        for pair in self.pattern_template:
            # Accumulate duration to get absolute expiry time
            expiry += pair["Expiry"]
            # Append dict with intensity and absolute expiry
            pattern.append({"Intensity": pair["Intensity"], "Expiry": expiry})
        # Return fully built pattern
        return pattern

    def get_intensity(self, current_time):
        # Check if vibe has expired
        if current_time >= self.expiry:
            # Return -1 to signal expiration (consumed by manager)
            return -1
        # Advance through pattern while current time exceeds segment expiry
        while current_time >= self.pattern[self.current_index]["Expiry"]:
            # Move to next pattern segment
            self.current_index += 1
            # Check if we've reached end of pattern
            if self.current_index > len(self.pattern) - 1:
                # Loop back to start
                self.current_index = 0
                # Rebuild pattern for next cycle
                self.pattern = self._build_pattern(current_time)
        # Return intensity of current segment
        return self.pattern[self.current_index]["Intensity"]


class PermanentVibe(Vibe):
    # Subclass for vibes that run indefinitely until manually stopped
    def __init__(self, pattern, trigger, current_time):
        # Call parent with no duration limit (permanent mode)
        super().__init__(pattern=pattern, trigger=trigger, current_time=current_time)


class TimedVibe(Vibe):
    # Subclass for vibes that run for a fixed duration then stop
    def __init__(self, pattern, trigger, total_duration, current_time):
        # Call parent with total_duration specified
        super().__init__(pattern=pattern, trigger=trigger, current_time=current_time, total_duration=total_duration)


class LoopedVibe(Vibe):
    # Subclass for vibes that repeat a pattern N times then stop
    def __init__(self, pattern, trigger, loop_count, current_time):
        # Call parent with loop_count specified
        super().__init__(pattern=pattern, trigger=trigger, current_time=current_time, loop_count=loop_count)


# ── VibeManager ───────────────────────────────────────────────────────────

class VibeManager:
    # Core manager for all active vibes; translates triggers to actuator commands
    def __init__(self, get_devices, max_vibe_intensity=1.0, scale_by_max=False,
                 beep_enabled=False, on_intensity_change=None):
        # Flag tracking whether vibration is stopped or active
        self.stopped = True
        # Current time (updated each frame by update())
        self.current_time = 0
        # Dict mapping trigger name -> list of active Vibe objects
        self.vibes = {}
        # Current calculated intensity (before clamping)
        self.current_intensity = 0
        # Real intensity after clamping to max (what's sent to devices)
        self.real_intensity = 0
        # Callable to get list of connected devices
        self._get_devices = get_devices
        # Maximum vibe intensity allowed (0.0-1.0)
        self._max_vibe_intensity = max_vibe_intensity
        # Whether to scale all intensities by max_vibe_intensity
        self._scale_by_max = scale_by_max
        # Whether to emit beep sounds on intensity changes
        self._beep_enabled = beep_enabled
        # Optional callback triggered when intensity changes: on_intensity_change(current, real)
        self._on_intensity_change = on_intensity_change

    def _add_vibe(self, vibe):
        # Only add if vibration is active (not stopped)
        if not self.stopped:
            # Create trigger key if needed, then append vibe to its list
            self.vibes.setdefault(vibe.trigger, []).append(vibe)

    def add_permanent_vibe(self, amount, trigger):
        # Create a permanent vibe with constant intensity
        self._add_vibe(PermanentVibe([[amount, 60]], trigger, self.current_time))

    def add_timed_vibe(self, amount, trigger, duration):
        # Create a timed vibe (one pulse of given duration)
        self._add_vibe(TimedVibe([[amount, duration]], trigger, duration, self.current_time))

    def add_permanent_pattern(self, pattern, trigger):
        # Create a permanent vibe with repeating pattern (list of [intensity, duration] pairs)
        self._add_vibe(PermanentVibe(pattern, trigger, self.current_time))

    def add_timed_pattern(self, pattern, trigger, duration):
        # Create a timed vibe with repeating pattern that expires after duration
        self._add_vibe(TimedVibe(pattern, trigger, duration, self.current_time))

    def add_looped_pattern(self, pattern, trigger, loop_count):
        # Create a looped vibe that repeats pattern N times then stops
        self._add_vibe(LoopedVibe(pattern, trigger, loop_count, self.current_time))

    def _remove_vibe(self, vibe):
        # Remove specific vibe from its trigger's list
        self.vibes[vibe.trigger].remove(vibe)
        # Clean up empty trigger entries
        if not self.vibes[vibe.trigger]:
            # Delete trigger key if no vibes remain
            del self.vibes[vibe.trigger]

    def remove_vibe_by_trigger(self, trigger, index=0):
        # Remove a vibe by trigger name (default: first one)
        if self.vibe_exists_for_trigger(trigger):
            # Get vibe at specified index
            vibe = self._get_vibes([trigger])[index]
            # Remove it
            self._remove_vibe(vibe)

    def toggle_vibe_to_condition(self, trigger, intensity, condition):
        # Check if a permanent vibe already exists for this trigger
        exists = self.vibe_exists_for_trigger(trigger)
        # Add vibe if condition is true and it doesn't exist
        if condition and not exists:
            # Create permanent vibe at specified intensity
            self.add_permanent_vibe(intensity, trigger)
        # Remove vibe if condition is false and it exists
        elif not condition and exists:
            # Remove the vibe
            self.remove_vibe_by_trigger(trigger)

    def toggle_pattern_to_condition(self, trigger, pattern, condition):
        # Check if a permanent pattern vibe exists for this trigger
        exists = self.vibe_exists_for_trigger(trigger)
        # Add pattern if condition is true and it doesn't exist
        if condition and not exists:
            # Create permanent pattern vibe
            self.add_permanent_pattern(pattern, trigger)
        # Remove pattern if condition is false and it exists
        elif not condition and exists:
            # Remove the vibe
            self.remove_vibe_by_trigger(trigger)

    def clear_vibes(self, triggers=None):
        # Clear all vibes if no specific triggers specified
        if triggers is None:
            # Wipe entire vibes dict
            self.vibes.clear()
        else:
            # Clear only specified triggers
            for trigger in triggers:
                # Delete trigger key if present
                if trigger in self.vibes:
                    del self.vibes[trigger]

    def clear_vibes_matching_regex(self, regex_pattern):
        # Compile regex pattern for matching
        regex = re.compile(regex_pattern)
        # Find all trigger names matching the pattern
        triggers = [t for t in self.vibes.keys() if regex.match(t)]
        # Clear matched triggers
        self.clear_vibes(triggers)

    def _get_vibes(self, triggers=None):
        # Get all vibes; default to all triggers if none specified
        if triggers is None:
            # Use all triggers in dict
            triggers = self.vibes.keys()
        # Accumulate all vibes from requested triggers
        out = []
        # Iterate through each trigger
        for t in triggers:
            # Extend output with all vibes for this trigger (or empty list if none)
            out.extend(self.vibes.get(t, []))
        # Return flattened list of vibes
        return out

    def vibe_exists_for_trigger(self, trigger):
        # Check if any vibe is active for the given trigger
        return bool(self._get_vibes([trigger]))

    def vibe_for_trigger_created_within_seconds(self, trigger, seconds):
        # Check if any vibe for trigger was created recently (within seconds ago)
        return any(
            # Compare vibe creation time to current time minus threshold
            v.creation_time > self.current_time - seconds
            # Check all vibes for this trigger
            for v in self._get_vibes([trigger])
        )

    def count_vibes_for_trigger(self, trigger):
        # Return number of active vibes for a trigger
        return len(self._get_vibes([trigger]))

    def _get_total_intensity(self, triggers=None):
        # Sum intensities from all active vibes (or filtered by triggers)
        total = 0
        # Iterate through all vibes (optionally filtered)
        for vibe in self._get_vibes(triggers):
            # Get intensity from this vibe at current time
            intensity = vibe.get_intensity(self.current_time)
            # Check if vibe has expired
            if intensity == -1:
                # Remove expired vibe
                self._remove_vibe(vibe)
            else:
                # Add intensity to running total
                total += intensity
        # Return sum of all active intensities
        return total

    async def _update_intensity_for_devices(self, devices):
        # Send intensity commands to all connected devices
        for device in devices:
            try:
                # Prepare intensities for all actuators on this device
                actuator_intensities = []
                # Iterate through each actuator
                for actuator in device.actuators:
                    # Get step size from actuator (e.g., 1/100 for 100 steps)
                    step = 1 / actuator.step_count
                    # Calculate max intensity in terms of device steps
                    actuator_max = round_value_to_nearest_step(self._max_vibe_intensity, step)
                    # Ensure max doesn't exceed intensity limit
                    while actuator_max > self._max_vibe_intensity:
                        # Reduce by one step if needed
                        actuator_max -= step
                    # Clamp real intensity to device-specific max and step size
                    actuator_intensity = clamp_value(
                        round_value_to_nearest_step(self.real_intensity, step),
                        actuator_max, value_name="actuator intensity",
                    )
                    # Record intensity for this actuator
                    actuator_intensities.append(actuator_intensity)
                    # Send command to actuator asynchronously
                    await actuator.command(actuator_intensity)
                # Build status line listing all actuator intensities
                line = f"[{device.name}] " + ", ".join(
                    f"Vibe {i+1}: {v}" for i, v in enumerate(actuator_intensities)
                )
                # Print status
                print(line)
            except Exception as err:
                # Log error and stop device on failure
                print(f"Stopping {device.name} due to an error while altering its vibration.")
                print(err)
                # Stop device async
                await device.stop()

    def print_active_triggers(self):
        # Print list of currently active vibe triggers
        active = []
        # Iterate through triggers and their vibes
        for trigger, vibes in self.vibes.items():
            # Get count of vibes for this trigger
            n = len(vibes)
            # Format: trigger name or "trigger (xN)" if multiple
            active.append(trigger if n == 1 else f"{trigger} (x{n})")
        # Print if any active
        if active:
            # Print comma-separated list
            print(f"  {', '.join(active)}")

    async def stop_all_devices(self):
        # Shut down all vibration and clear vibes
        # Mark as stopped
        self.stopped = True
        # Clear all vibes
        self.clear_vibes()
        # Iterate through all devices
        for device in self._get_devices():
            # Send stop command to device
            await device.stop()
        # Reset intensities
        self.current_intensity = 0
        self.real_intensity = 0
        # Log action
        print("Stopped all devices.")
        # Notify callback if registered
        if self._on_intensity_change:
            # Call with both intensities at zero
            self._on_intensity_change(0, 0)

    async def update(self, current_time):
        # Main per-frame update method; called each frame with current time
        # Check if vibration is stopped
        if self.stopped:
            # Only stop devices if intensity is not already zero
            if self.current_intensity != 0:
                # Cleanly shut down
                await self.stop_all_devices()
            # Exit early, no processing needed
            return
        # Update current time for all vibes
        self.current_time = current_time
        # Calculate total intensity from all active vibes
        latest = self._get_total_intensity()
        # Apply scaling by max intensity if enabled
        if self._scale_by_max:
            # Multiply by max factor
            latest *= self._max_vibe_intensity
        # Ensure positive and round to 4 decimals
        latest = abs(round(latest, 4))
        # Check if intensity has changed
        if self.current_intensity != latest:
            # Update current intensity
            self.current_intensity = latest
            # Clamp to maximum allowed
            clamped = clamp_value(self.current_intensity, self._max_vibe_intensity, value_name="intensity")
            # Log change (show clamped value if different)
            if self.current_intensity == clamped:
                # No clamping needed
                print(f"Updated intensity: {self.current_intensity}")
            else:
                # Show both original and clamped
                print(f"Updated intensity: {self.current_intensity} ({clamped})")
            # Check if clamped intensity changed
            if self.real_intensity != clamped:
                # Update real intensity
                self.real_intensity = clamped
                # Print currently active triggers
                self.print_active_triggers()
                # Send intensity to all devices
                await self._update_intensity_for_devices(self._get_devices())
            # Notify callback if registered
            if self._on_intensity_change:
                # Call callback with both intensities
                self._on_intensity_change(self.current_intensity, self.real_intensity)
            # Optionally emit beep on intensity change
            if self._beep_enabled:
                try:
                    # Import winsound module
                    import winsound
                    # Beep at frequency based on intensity: 1000Hz + (intensity * 5000Hz)
                    winsound.Beep(int(1000 + (self.real_intensity * 5000)), 20)
                except Exception:
                    # Silently ignore beep errors
                    pass
