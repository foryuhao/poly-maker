"""M18 original SOP: a first, larger SELL shock with contained depth loss.

M16's fixed-Bid campaign clock, provenance checks, depth invalidation, generation
handling and resource release are shared lifecycle plumbing. Round accumulation,
reference eligibility, comparisons, maintenance and publication are M18-specific;
no speed interval, receiver-quantum proof or per-print recheck is inherited.
"""

from dataclasses import dataclass
from decimal import Decimal as D

from .m12 import M12, parameter_identity
from .m16 import M16 as BidLifecycle, Campaign, ObservedRound
from .m18_path import RoundPath, number

VERSION = "m18-fixed-anchor-entry-v1/raw-terminal-depth-r1"
SHORT_ROUND_MARKER = "M18_250MS_KEEP_100MS_HALF_RECOVERY_R3"
SHORT_ROUND_PROFILE = "M18_EXPLICIT_250MS_KEEP_STRICT_RECOVERY_NUMERICAL_V1"


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .5
    stable_seconds: float = .1
    stable_recovery: D = D(".50")
    reference_hit_shares_min: D = D("5")
    reference_hit_fraction_min: D = D(".10")
    reference_loss_fraction_min: D = D(".05")
    initial_depth_ratio_min: D = D(".80")
    initial_depth_ratio_max: D = D("1.25")
    hit_ratio_min: D = D("2")
    hit_ratio_max: D = D("4")
    absolute_loss_ratio_max: D = D("1")
    normalized_loss_ratio_max: D = D("1")
    pair_gap: float = 2.
    ttl: float = 3.


def signal_version(parameters):
    scale = "/explicit-bid250-stable100" if parameters.round_seconds == .25 else ""
    return VERSION + scale + "/p-" + parameter_identity(parameters)[1][:12]


class M18(BidLifecycle):
    strategy = "M18"

    def __init__(self, emit, parameters=None):
        if parameters is not None and not isinstance(parameters, Parameters):
            raise ValueError("M18_PARAMETERS_REQUIRED")
        M12.__init__(self, emit, parameters or Parameters())
        if (self.p.round_seconds not in (.25, .5)
                or (self.p.stable_seconds, self.p.stable_recovery) != (.1, D(".50"))):
            raise ValueError("M18_FIXED_STRICT_RECOVERY_CONTRACT_CHANGED")
        self.version = signal_version(self.p)
        self.campaigns, self.campaign_ends = {}, {}
        self.closed_windows, self.blocked_rounds = {}, {}

    def end_campaign(self, token, reason):
        campaign = self.campaigns.pop(token, None)
        if campaign is None:
            return
        self.campaign_ends[token] = self.boundary_key()
        if campaign.ready:
            self.components.pop(campaign.ready["id"], None)
        self.event("AMPLITUDE_CAMPAIGN_ENDED", id=campaign.id, token=token, reason=reason)
        if campaign.signal_id and self.signals.pop(campaign.signal_id, None) is not None:
            self.event("SIGNAL_ENDED", id=campaign.signal_id, reason=reason)

    def requires_pre_hit_for_trade(self, *, token, side, price, received, pre_event_best_bid):
        if side != "SELL" or any(r.token == token for r in self.rounds.values()):
            return False
        campaign = self.campaigns.get(token)
        return price == (campaign.price if campaign else pre_event_best_bid)

    def trade(self, *, token, side, price, quantity, received, q0, generation,
              input_ref=None, source=None, pre_event_best_bid=None):
        if side != "SELL":
            return
        # Only the locked anchor is relevant once a token owns a round/campaign.
        current = next((r for r in self.rounds.values() if r.token == token), None)
        campaign = self.campaigns.get(token)
        anchor = current.price if current else campaign.price if campaign else pre_event_best_bid
        if price != anchor:
            return
        point = self.point(token=token, price=price, quantity=quantity, generation=generation,
                           received=received, input_ref=input_ref, source=source, kind="DIRECT_SELL")
        if point is None:
            return
        key = token, "BID", price
        start_key = (self.now, *input_ref)
        if current is not None:
            if number(received) < current.path.end:
                current.path.observe(point)
                if current.path.failure:
                    self.invalidate(token, current.path.failure)
            else:
                # Do not relabel an already-started physical round after R1
                # finally becomes available. Its first SELL remains consumed.
                end = self.blocked_rounds.get(key)
                if end is None or number(received) >= end:
                    self.blocked_rounds[key] = number(received) + number(self.p.round_seconds)
                self.event("ROUND_NOT_STARTED", token=token, side="BID",
                           reason="PRIOR_WINDOW_NOT_CLOSED_NEW_PHYSICAL_ROUND_INELIGIBLE")
            return
        if number(received) < self.blocked_rounds.get(key, D("-Infinity")):
            self.event("ROUND_NOT_STARTED", token=token, side="BID",
                       reason="PHYSICAL_ROUND_STARTED_BEFORE_REFERENCE_AVAILABLE")
            return
        last = self.closed_windows.get(key)
        if last and number(received) < last[0]:
            self.invalidate(token, "LATE_TRADE_TO_CLOSED_ROUND")
            return
        if q0 <= 0:
            self.event("ROUND_NOT_STARTED", token=token, side="BID", reason="NO_PRE_HIT_LEVEL")
            return
        if len(self.rounds) >= 512:
            raise RuntimeError("SOP_ROUND_CAPACITY")
        self.serial += 1
        identity = f"M18:{generation}:{token}:BID:{self.serial}"
        role = "INELIGIBLE"
        if campaign is not None and generation != campaign.generation:
            self.invalidate(token, "BOOK_GENERATION_CHANGED")
            return
        after_close = last is None or number(received) > number(last[1])
        if campaign:
            if campaign.ready is not None:
                role = "FOLLOWUP"
            elif (campaign.second_id is None and after_close
                  and number(received) > number(campaign.reference["key"][0])
                  and start_key > campaign.reference["key"]):
                role, campaign.second_id = "SECOND", identity
        else:
            ended = self.campaign_ends.get(token)
            if after_close and (ended is None or (number(received) > number(ended[0]) and start_key > ended)):
                role = "REFERENCE_CANDIDATE"
        path = RoundPath(token=token, price=price, generation=generation, start=received, q0=q0,
                         round_seconds=self.p.round_seconds)
        current = ObservedRound(identity, token, price, generation, start_key, path, role, q0)
        self.rounds[key] = current
        path.observe(point)
        self.event("ROUND_STARTED", id=identity, token=token, side="BID", price=price,
                   generation=generation, received=received, q0=q0, role=role,
                   raw_input_ref=input_ref, pre_event_best_bid=pre_event_best_bid)
        if path.failure:
            self.invalidate(token, path.failure)

    def close(self, watermark, covered):
        for key, current in tuple(self.rounds.items()):
            if self.rounds.get(key) is not current or number(watermark) <= current.path.end:
                continue
            del self.rounds[key]
            result = current.path.finish(available=self.now, watermark=watermark,
                                         coverage_proven=covered(current.token, float(current.path.start),
                                                                float(current.path.end)))
            self.closed_windows[key] = current.path.end, self.now
            fact = {**result, "id": current.id, "side": "BID", "role": current.role,
                    "key": self.boundary_key(), "window_end": float(current.path.end),
                    "u": result["hits"] / result["q0"], "d": result["loss"] / result["q0"],
                    "r": result["recovery"], "guard": result["qmin"] + D(".40") * result["loss"],
                    "start_key": current.start_key, "current_quantity": current.current_quantity}
            current.path.release()
            self.event("ROUND_CLOSED", **fact)
            if not result["data_proven"]:
                self.event("ROUND_UNKNOWN", id=current.id, reason=result["reason"])
            campaign = self.campaigns.get(current.token)
            if campaign is not None:
                if campaign.ready is not None and current.id != campaign.ready["id"]:
                    self.followup(campaign, fact)
                elif campaign.second_id == current.id:
                    self.second(campaign, fact)
                continue
            ended = self.campaign_ends.get(current.token)
            if (current.role != "REFERENCE_CANDIDATE"
                    or (ended is not None and (current.start_key <= ended
                                               or current.path.start <= number(ended[0])))):
                continue
            checks = {
                "strict_stable_and_terminal": result["status"] == "ROUND_PROVEN",
                "reference_hit": result["hits"] >= max(self.p.reference_hit_shares_min,
                                                      self.p.reference_hit_fraction_min * result["q0"]),
                "reference_loss": fact["d"] >= self.p.reference_loss_fraction_min,
                "original_level_retained": not result["post_window_missing"] and current.current_quantity > 0,
            }
            self.event("AMPLITUDE_REFERENCE_REVIEW", id=current.id, checks=checks)
            if not all(checks.values()):
                continue
            deadline = float(number(self.now) + number(self.p.pair_gap))
            self.campaigns[current.token] = Campaign(current.id, current.token, current.price,
                                                     current.generation, fact, deadline, current.current_quantity)
            self.event("AMPLITUDE_REFERENCE_CONFIRMED", **fact, reference_only=True,
                       historical_floor_is_not_a_formation_guard=True, pair_deadline=deadline)

    def second(self, campaign, fact):
        if number(self.now) >= number(campaign.pair_deadline):
            self.end_campaign(campaign.token, "SECOND_CONFIRMATION_GAP_EXPIRED")
            return
        if fact["status"] != "ROUND_PROVEN":
            self.end_campaign(campaign.token, "FIRST_SECOND_ROUND_FAILED_OR_TERMINAL_UNKNOWN")
            return
        reference = campaign.reference
        ratios = {"initial_depth": fact["q0"] / reference["q0"],
                  "hit": fact["hits"] / reference["hits"],
                  "absolute_loss": fact["loss"] / reference["loss"],
                  "normalized_loss": fact["d"] / reference["d"]}
        loss_minimum = max(D(".5"), D(".0001") * fact["q0"])
        allowed_loss_maximum = min(self.p.absolute_loss_ratio_max * reference["loss"],
                                   self.p.normalized_loss_ratio_max * reference["d"] * fact["q0"])
        checks = {
            "loss_interval_nonempty": loss_minimum <= allowed_loss_maximum,
            "initial_depth": self.p.initial_depth_ratio_min <= ratios["initial_depth"] <= self.p.initial_depth_ratio_max,
            "hit": self.p.hit_ratio_min <= ratios["hit"] <= self.p.hit_ratio_max,
            "absolute_loss": ratios["absolute_loss"] <= self.p.absolute_loss_ratio_max,
            "normalized_loss": ratios["normalized_loss"] <= self.p.normalized_loss_ratio_max,
            "recovery_not_worse": fact["r"] >= reference["r"],
        }
        self.event("AMPLITUDE_PAIR_REVIEW", root_id=campaign.id, second_id=fact["id"],
                   ratios=ratios, checks=checks, allowed_loss_interval=[loss_minimum, allowed_loss_maximum])
        if not all(checks.values()):
            self.end_campaign(campaign.token, "LOSS_INTERVAL_EMPTY" if not checks["loss_interval_nonempty"]
                              else "FIRST_SECOND_ROUND_COMPARISON_FAILED")
            return
        if (fact["post_window_missing"] or fact["current_quantity"] <= 0
                or (fact["post_window_min"] is not None and fact["post_window_min"] < fact["guard"])):
            self.event("PUBLICATION_REJECTED", id=fact["id"], reason="POST_WINDOW_GUARD")
            self.end_campaign(campaign.token, "POST_WINDOW_GUARD")
            return
        campaign.ready = {**fact, "deadline": float(number(self.now) + number(self.p.ttl)),
                          "strength_ratios": ratios}
        self.components[fact["id"]] = campaign.ready

    def followup(self, campaign, fact):
        if not fact["data_proven"]:
            self.invalidate(campaign.token, "SUBSEQUENT_ROUND_UNKNOWN:" + str(fact["reason"]))
        elif fact["qualified"] and not fact["stable"]:
            self.end_campaign(campaign.token, "SUBSEQUENT_ROUND_COUNTEREVIDENCE")
        # Later stable rounds need not double H, and never renew the deadline.

    def publish(self):
        for token, campaign in tuple(self.campaigns.items()):
            if campaign.ready is None or campaign.signal_id:
                continue
            second = campaign.ready
            if (self.now != second["key"][0] or number(self.now) >= number(campaign.pair_deadline)
                    or number(self.now) >= number(second["deadline"])
                    or campaign.current_quantity <= 0 or campaign.current_quantity < second["guard"]):
                self.end_campaign(token, "PUBLICATION_BOUNDARY_OR_ORIGINAL_LEVEL_GUARD")
                continue
            identity = f"M18:pair:{campaign.id}"
            signal = {"id": identity, "token": token, "root_id": campaign.id, "version": self.version,
                      "component_ids": [campaign.id, second["id"]], "confirmed": self.now,
                      "deadline": second["deadline"], "anchor_price": campaign.price,
                      "generation": campaign.generation, "bid": dict(second),
                      "prior_reference": dict(campaign.reference), "quality_status": "PROVEN",
                      "reference_only_before_confirmation": True,
                      "post_window_prepublication_guard_status": "PROVEN",
                      "strength_ratios": second["strength_ratios"]}
            campaign.signal_id = identity
            self.signals[identity] = signal
            self.event("SIGNAL_CONFIRMED", **signal)
