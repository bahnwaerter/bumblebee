from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Generic, TypeVar
from uuid import UUID

from vm_manager.models import Instance

S = TypeVar('S')
T = TypeVar('T')


class StatusMapper(Generic[S, T]):
    """
    Status of a Cloud object.
    """

    def __init__(self, status_mapping: Dict[S, T], default_value: T):
        self._status_mapping: Dict[S, T] = status_mapping
        self._default_value: T = default_value

    def _get_mapped_status(self, status: S) -> T:
        return self._status_mapping.get(status)

    def _get_default_value(self) -> T:
        return self._default_value

    def get_status(self, status: S) -> T:
        value = self._get_mapped_status(status)
        return value if value else self._get_default_value()

    @staticmethod
    def create(status_mapping: Dict[S, T],
               default_value: T) -> "StatusMapper[S, T]":
        return StatusMapper[S, T](status_mapping, default_value)


@dataclass
class Object(object):
    """
    Cloud connector object.
    """
    id: UUID
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict] = None
    zone: Optional[str] = None


@dataclass
class Server(Object):
    """
    Cloud server.
    """

    @staticmethod
    def create(instance: Instance) -> "Server":
        server_id = instance.id
        return Server(id=server_id)


class ServerStatus(Enum):
    """
    Status of a cloud server.
    """
    # default status
    UNKNOWN = 0

    # fault status
    ERROR = 1
    DELETED = 2

    # progress status
    ACTIVE = 20
    BUILD = 21
    REBUILD = 22
    RESIZE = 23
    VERIFY_RESIZE = 24
    MIGRATING = 25

    # other status
    RESCUE = 40
    REVERT_RESIZE = 41
    SHELVED = 42
    SHELVED_OFFLOADED = 43
    SOFT_DELETED = 44
    PAUSED = 45
    SUSPENDED = 46
    SHUTOFF = 47
    REBOOT = 48
    HARD_REBOOT = 49
    PASSWORD = 50


@dataclass
class Volume(Object):
    """
    Cloud volume.
    """
    bootable: Optional[bool] = None

    @staticmethod
    def create(instance: Instance) -> "Volume":
        volume_id = instance.id
        return Volume(id=volume_id)


class VolumeStatus(Enum):
    """
    Status of a cloud volume.
    """
    # default status
    UNKNOWN = 0

    # main status
    CREATING = 1
    AVAILABLE = 2
    IN_USE = 3
    DELETING = 4

    # fault status
    ERROR = 20
    ERROR_DELETING = 21
    ERROR_MANAGING = 22
    ERROR_RESTORING = 23
    ERROR_BACKING_UP = 24
    ERROR_EXTENDING = 25

    # other status
    MANAGING = 40
    ATTACHING = 41
    DETACHING = 42
    MAINTENANCE = 43
    RESTORING_BACKUP = 44
    RESERVED = 45
    AWAITING_TRANSFER = 46
    BACKING_UP = 47
    DOWNLOADING = 48
    UPLOADING = 49
    RETYPING = 50
    EXTENDING = 51


@dataclass
class VolumeBackup(Object):
    pass


@dataclass
class Flavor(Object):
    """
    Flavor for a cloud server.
    """
    ram_size: Optional[int] = None
    disk_size: Optional[int] = None
    num_vcpus: Optional[int] = None


@dataclass
class Network(Object):
    """
    Cloud network.
    """
    pass


class ConsoleProtocol(Enum):
    """
    Protocol of a cloud server console.
    """
    VNC = 1
    SPICE = 2


@dataclass
class ConsoleInfo(object):
    """
    Console of a cloud server.
    """
    protocol: ConsoleProtocol
    host: str
    port: int
