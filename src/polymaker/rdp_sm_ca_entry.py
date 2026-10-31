"""CA NORMAL admission and one-shot ownership; REPAIR remains independent BA."""

from dataclasses import dataclass
from decimal import Decimal

from polymaker.rdp_sm_signal import Signal

MIN_CONFIRMATION_SECONDS = Decimal("0.100")


def confirmation_duration_ms(episode):
    """Formation duration, never the age of an already confirmed signal."""
    if episode.get("tC") is None:
        return None
    return (Decimal(str(episode["tC"])) - Decimal(str(episode["t0"]))) * 1000


def confirmation_allowed(episode):
    duration = confirmation_duration_ms(episode)
    return duration is not None and duration > MIN_CONFIRMATION_SECONDS * 1000


@dataclass(frozen=True)
class CAEntryEpisode:
    episode_id: str
    token_id: str
    outcome: str
    anchor_bid_price: Decimal
    signal_start_mono: float
    order_deadline: float


class CounterAttackEntry:
    def __init__(self, feed):
        self.feed = feed
        self.selected = self.claimed = None
        self.episode_id = self.episode_start = None
        self.consumed = set()
        self.block_reason = None

    def evaluate(self, snapshot):
        with self.feed.lock:
            observation = self.feed.observe_ca("snapshot")
            valid = []
            self.block_reason = None
            if observation and observation.get("data_complete") and observation.get("recording_active"):
                # Apply BA's conservative conflict policy to both unexpired CA
                # formations, before filtering candidate age or consumption.
                active = [s for s in observation["outcomes"].values()
                          if s["confirmed"] and s["episode"]["end_reason"] is None]
                if len(active) > 1:
                    self.feed.observe_ca("invalidate", "CA_BOTH_SIDES_VALID")
                elif len(active) == 1:
                    s = active[0]
                    ep = s["episode"]
                    book = self.feed.books[ep["token_id"]]
                    if not confirmation_allowed(ep):
                        self.block_reason = "CA_CONFIRMATION_TOO_FAST"
                    elif (s["candidate_usable"] and ep["episode_id"] not in self.consumed
                            and snapshot.valid and snapshot.generation == ep["generation"]
                            and book.best_bid == Decimal(ep["anchor_bid_price"])):
                        valid.append(CAEntryEpisode(ep["episode_id"], ep["token_id"], ep["outcome"],
                            Decimal(ep["anchor_bid_price"]), ep["t0"], ep["deadline"]))
            self.selected = valid[0] if valid else None
            self.episode_id = self.selected.episode_id if self.selected else None
            self.episode_start = self.selected.signal_start_mono if self.selected else None
            return Signal("VALID_"+self.selected.outcome if self.selected else "NONE",
                          snapshot.snapshot_id, event_mono=self.episode_start)

    def claim(self, signal):
        with self.feed.lock:
            ep = self.selected
            if self.claimed or ep is None or not signal.direction or ep.episode_id in self.consumed:
                return None
            self.claimed = (ep.episode_id, ep.outcome, signal.snapshot_id)
            return self.claimed

    def current(self, claim):
        with self.feed.lock:
            if claim is None or claim != self.claimed or self.selected is None:
                return False
            if claim[0] != self.selected.episode_id or claim[0] in self.consumed:
                return False
            observation = self.feed.observe_ca("snapshot")
            return bool(observation and observation.get("data_complete")
                        and observation.get("recording_active")
                        and any(s["episode_id"] == claim[0] and s["candidate_usable"]
                                and confirmation_allowed(s["episode"])
                                for s in observation["outcomes"].values()))

    def consume(self, claim):
        with self.feed.lock:
            if not self.current(claim):
                raise ValueError("CA_EPISODE_CHANGED")
            self.consumed.add(claim[0])

    def release(self, claim):
        with self.feed.lock:
            if claim == self.claimed:
                self.claimed = None

    def active_reason(self, claim, snapshot):
        with self.feed.lock:
            observation = self.feed.observe_ca("snapshot")
            if (not observation or not observation.get("data_complete")
                    or not observation.get("recording_active")):
                return "CA_OBSERVER_UNAVAILABLE"
            ca = self.feed.ca
            ep = next((e for e in ca.states.values() if claim and e.episode_id == claim[0]), None)
            if ep is None:
                return "CA_EPISODE_CHANGED"
            if ep.end_reason:
                return ep.end_reason
            book = self.feed.books[ep.token_id]
            if not snapshot.valid or not ca.legal(book, self.feed.monotonic()):
                return snapshot.reason or "CA_BOOK_INVALID"
            return "CA_ANCHOR_INVALID" if book.best_bid != ep.anchor_bid_price else None
