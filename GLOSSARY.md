# Skylink Orbit

Orbit Home connects an account to physical garage hubs and their doors.

## Language

**Hub**: A physical opener/controller identified by `hub_id`. A hub can expose more than one door.

**Door channel**: A door controlled by a hub: A or B for NOVA, A for NVMini, or the single unnamed GDO channel.
_Avoid_: Position (the wire field uses this word for channel selection, not a percentage).

**Activation**: The native Orbit main-button command. Its physical effect depends on device state and has not yet been validated on the owner's opener.
_Avoid_: Open command, close command, stop command.

**Snapshot**: The full account device list returned by the broker after a discovery request.
