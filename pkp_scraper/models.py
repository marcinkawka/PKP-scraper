from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Carrier:
    code: str
    name: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class Station:
    id: int
    name: str


@dataclass(frozen=True)
class Train:
    schedule_id: int
    order_id: int
    train_order_id: int
    carrier_code: str | None
    category: str | None
    number: str | None
    name: str | None
    intl_number: str | None


@dataclass(frozen=True)
class Stop:
    seq: int                        # plannedSequenceNumber
    station_id: int
    seq_shift: int                  # planned - actual sequence number
    planned_arr: datetime | None
    planned_dep: datetime | None
    actual_arr: datetime | None
    actual_dep: datetime | None
    confirmed: bool                 # False = forecast, not a measured time


@dataclass(frozen=True)
class TrainRun:
    schedule_id: int
    order_id: int
    train_order_id: int
    operating_date: date
    status: str
    stops: tuple[Stop, ...] = ()


@dataclass(frozen=True)
class DisruptionType:
    code: str
    message: str


@dataclass(frozen=True)
class Disruption:
    schedule_id: int
    order_id: int
    train_order_id: int
    operating_date: date
    seq: int                        # sequenceNumber == stop.seq
    station_id: int
    type_code: str


@dataclass(frozen=True)
class DelayedTrain:
    """A train's last confirmed stop that arrived late. Read model, not stored."""
    carrier_code: str | None
    category: str | None
    number: str | None
    name: str | None
    station_name: str
    planned_arr: datetime | None
    delay_min: int


@dataclass(frozen=True)
class TrainDisruption:
    """One disruption reported for a train at one station. Read model, not stored."""
    carrier_code: str | None
    category: str | None
    number: str | None
    train_name: str | None
    station_name: str
    message: str
    first_seen_at: datetime


@dataclass(frozen=True)
class StationDisruptions:
    """A station's disruptions of one day, with its traffic. Read model, not stored."""
    station_name: str
    trains: int                     # trains stopping there that day (pkp.stop)
    disrupted_trains: int           # of those, trains with at least one disruption there
    disruptions: tuple[TrainDisruption, ...]


@dataclass(frozen=True)
class StationTrains:
    """How many times a disruption was reported at a station, and for which trains. Read model."""
    station_name: str
    count: int
    trains: tuple[str, ...]         # train name, else "category number"; repeats kept
