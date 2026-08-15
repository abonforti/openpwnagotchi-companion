"""Stand-in for `pwnagotchi.mesh.peer`.

Pinned by SPEC.md F16. The members, and the two traps they carry:

    attributes  first_met, first_seen, prev_seen  datetime, or str when Peer's
                                                  own constructor fell back
                last_seen                         already a float epoch
                encounters, session_id, last_channel, rssi, adv
    methods     inactive_for(), first_encounter(), is_good_friend(), face(),
                name(), identity(), full_name(), version(), pwnd_run(),
                pwnd_total(), uptime(), epoch(), is_closer()

Trap one is the timestamp asymmetry: three datetimes and one float, sitting next
to each other, all four of which leave the plugin as epoch floats.

Trap two is that the advertisement-backed accessors raise when the key is
missing, exactly as the real ones do. SPEC 2.8 requires the plugin to guard
every accessor individually, so this stub must not soften them into `.get`
lookups — that would make a missing key untestable.

The constructor signature is a test-side convenience: the plugin only ever reads
peers out of `agent._peers` and never builds one, so nothing in the contract
depends on how a Peer comes into existence.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any


class Peer:
    def __init__(
        self,
        adv: dict[str, Any] | None = None,
        *,
        first_met: Any = None,
        first_seen: Any = None,
        prev_seen: Any = None,
        last_seen: float | None = None,
        encounters: int = 1,
        session_id: str = "aa:bb:cc:dd:ee:ff",
        last_channel: int = 1,
        rssi: int = -50,
    ) -> None:
        now = time.time()
        moment = datetime.fromtimestamp(now)
        self.adv: dict[str, Any] = {} if adv is None else adv
        # datetime, or whatever was handed in - a str reproduces the real
        # constructor's parse fallback.
        self.first_met = moment if first_met is None else first_met
        self.first_seen = moment if first_seen is None else first_seen
        self.prev_seen = moment if prev_seen is None else prev_seen
        # a float epoch, not a datetime. This is the asymmetry.
        self.last_seen = now if last_seen is None else last_seen
        self.encounters = encounters
        self.session_id = session_id
        self.last_channel = last_channel
        self.rssi = rssi

    # -- advertisement-backed accessors ------------------------------------

    def name(self) -> str:
        return self.adv.get("name", "???")

    def identity(self) -> str:
        return self.adv.get("identity", "???")

    def full_name(self) -> str:
        return "%s@%s" % (self.name(), self.identity())

    def version(self) -> str:
        return self.adv["version"]

    def face(self) -> str:
        return self.adv["face"]

    def uptime(self) -> int:
        return self.adv["uptime"]

    def epoch(self) -> int:
        return self.adv["epoch"]

    def pwnd_run(self) -> int:
        value = self.adv["pwnd_run"]
        return len(value) if isinstance(value, (list, tuple, set)) else int(value)

    def pwnd_total(self) -> int:
        return int(self.adv["pwnd_tot"])

    # -- the rest of the pinned surface ------------------------------------

    def inactive_for(self) -> float:
        return time.time() - self.last_seen

    def first_encounter(self) -> bool:
        return self.encounters == 1

    def is_good_friend(self, config: Any) -> bool:
        return False

    def is_closer(self, other: "Peer") -> bool:
        return self.rssi > other.rssi
