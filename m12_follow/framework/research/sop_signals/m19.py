"""M19 historical Ask reference -> first lighter/larger-loss Ask + live Bid.

Original SOP baseline only. Raw terminal quantities, first-attempt causality,
component clocks and complete-parent publication are independent of execution.
"""
from dataclasses import dataclass
from decimal import Decimal as D

from .m12 import M12, parameter_identity
from .m16 import M16 as RawProvenance, ObservedRound
from .m19_path import AnchorRoundPath, number

VERSION = "m19-fixed-anchor-entry-v1/raw-two-sided-terminal-r1"


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
    absolute_hit_ratio_min: D = D(".25")
    absolute_hit_ratio_max: D = D(".50")
    normalized_hit_ratio_min: D = D(".25")
    normalized_hit_ratio_max: D = D(".50")
    absolute_loss_ratio_min: D = D("1.25")
    normalized_loss_ratio_min: D = D("1.25")
    second_recovery_max: D = D(".30")
    ask_gap: float = 2.
    pair_gap: float = 1.5
    ttl: float = 3.


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


@dataclass
class AskRoot:
    reference: dict
    current_quantity: D
    second_id: str | None = None
    second: dict | None = None
    signal_id: str | None = None


class M19(M12):
    strategy = "M19"
    # Provenance/clock-shape helper only; no M16 speed or receiver-quantum rule.
    point = RawProvenance.point
    boundary_key = RawProvenance.boundary_key

    def __init__(self, emit, parameters=None):
        if parameters is not None and not isinstance(parameters, Parameters):
            raise ValueError("M19_PARAMETERS_REQUIRED")
        super().__init__(emit, parameters or Parameters())
        if (self.p.round_seconds, self.p.stable_seconds, self.p.stable_recovery) != (.5, .1, D(".50")):
            raise ValueError("M19_ORIGINAL_STRICT_ROUND_CONTRACT_CHANGED")
        self.version = signal_version(self.p)
        self.roots, self.root_ends = {}, {}
        self.closed_windows, self.closing_rounds = {}, {}
        self.pending_bids = set()

    def all_rounds(self):
        return (*self.closing_rounds.values(), *self.rounds.values())

    def end_root(self, token, reason):
        root = self.roots.pop(token, None)
        if root is None:
            return
        self.root_ends[token] = self.boundary_key()
        self.event("ASK_ROOT_ENDED", root_id=root.reference["id"], token=token, reason=reason)
        if root.signal_id and self.signals.pop(root.signal_id, None) is not None:
            self.event("SIGNAL_ENDED", id=root.signal_id, reason=reason)

    def end_component(self, identity, reason):
        component = self.components.pop(identity, None)
        if component is None:
            return
        self.pending_bids.discard(identity)
        self.event("COMPONENT_ENDED", id=identity, reason=reason)
        for token, root in tuple(self.roots.items()):
            signal = self.signals.get(root.signal_id)
            if signal and identity in signal["component_ids"]:
                self.end_root(token, reason)

    def clock(self, now, seq, subindex=-1):
        if number(now) < number(self.now):
            raise ValueError("SOP_AVAILABLE_CLOCK_REGRESSION")
        self.now, self.key = now, (now, seq, subindex)
        for identity, component in tuple(self.components.items()):
            if number(now) >= number(component["deadline"]):
                self.end_component(identity, "BID_COMPONENT_TTL_EXPIRED")
        for token, root in tuple(self.roots.items()):
            if root.signal_id:
                if number(now) >= number(self.signals[root.signal_id]["deadline"]):
                    self.end_root(token, "SIGNAL_TTL_EXPIRED")
            elif root.second is None:
                if number(now) >= number(root.reference["key"][0]) + number(self.p.ask_gap):
                    self.end_root(token, "ASK2_FORMATION_EXPIRED")
            elif number(now) >= number(root.second["key"][0]) + number(self.p.pair_gap):
                self.end_root(token, "ASK2_WAIT_FOR_BID_EXPIRED")
        self.closed_windows = {k:v for k,v in self.closed_windows.items() if v[1] >= now-4}
        self.root_ends = {k:v for k,v in self.root_ends.items() if v[0] >= now-4}

    def invalidate(self, token, reason):
        self.quality[token] = reason
        for collection in (self.rounds, self.closing_rounds):
            for key, current in tuple(collection.items()):
                if current.token == token:
                    current.path.release()
                    del collection[key]
                    self.event("ROUND_UNKNOWN", id=current.id, reason=reason)
        for identity, component in tuple(self.components.items()):
            if component["token"] == token:
                self.end_component(identity, reason)
        self.end_root(token, reason)
        self.event("QUALITY_UNKNOWN", token=token, reason=reason)

    def new_generation(self, token, generation):
        self.invalidate(token, "BOOK_RECONSTRUCTED")
        self.closed_windows = {k:v for k,v in self.closed_windows.items() if k[0] != token}
        self.quality.pop(token, None)
        self.event("BOOK_GENERATION_STARTED", token=token, generation=generation)

    def relevant(self, token, side, price):
        if any((r.token, r.side, r.price) == (token, side, price) for r in self.all_rounds()):
            return True
        if side == "ASK":
            root = self.roots.get(token)
            return root is not None and root.reference["price"] == price
        return any((c["token"], c["price"]) == (token, price) for c in self.components.values())

    def level_missing(self, token, side, price, received, *, frame, row, source):
        if self.relevant(token, side, price):
            self.event("ORIGINAL_LEVEL_ABSENT", token=token, side=side, price=price,
                       received=received, raw_frame=frame, raw_row=row, source=source,
                       quantity_endpoint=False)
            self.invalidate(token, "ORIGINAL_LEVEL_MISSING_IN_FULL_BOOK")

    def requires_pre_hit_for_trade(self, *, token, side, price, received,
                                   pre_event_best_bid=None, pre_event_best_ask=None):
        leg = "ASK" if side == "BUY" else "BID"
        if (token, leg, price) in self.rounds:
            return False
        if leg == "ASK":
            root = self.roots.get(token)
            active = next((r for r in self.rounds.values() if r.token == token and r.side == "ASK"), None)
            anchor = active.price if active else root.reference["price"] if root else pre_event_best_ask
            return price == anchor
        return price == pre_event_best_bid or self.relevant(token, "BID", price)

    def depth(self, token, side, price, quantity, received, *, generation=None,
              input_ref=None, source=None, kind=None):
        if not self.relevant(token, side, price):
            return
        if kind not in {side+"_BOOK", side+"_PRICE_CHANGE"}:
            self.invalidate(token, "EXPLICIT_ANCHOR_QUANTITY_EVENT_REQUIRED")
            return
        point = self.point(token=token, price=price, quantity=quantity, generation=generation,
                           received=received, input_ref=input_ref, source=source, kind=kind)
        if point is None:
            return
        closed = self.closed_windows.get((token, side, price))
        if closed and number(received) < closed[0]:
            self.invalidate(token, "LATE_INPUT_TO_CLOSED_ROUND")
            return
        for current in self.all_rounds():
            if (current.token, current.side, current.price) == (token, side, price):
                current.path.observe(point)
                current.current_quantity = quantity
                if current.path.failure:
                    self.invalidate(token, current.path.failure)
                    return
        if quantity <= 0:
            self.invalidate(token, "ORIGINAL_LEVEL_LOST")
            return
        if side == "BID":
            for identity, component in tuple(self.components.items()):
                if (component["token"], component["price"]) == (token, price):
                    component["current_quantity"] = quantity
                    if generation != component["generation"] or quantity < component["guard"]:
                        self.end_component(identity, "BID_ORIGINAL_LEVEL_GUARD")
        else:
            root = self.roots.get(token)
            if root and root.reference["price"] == price:
                root.current_quantity = quantity
                if generation != root.reference["generation"]:
                    self.end_root(token, "ASK_GENERATION_CHANGED")
                elif (root.second and number(received) >= number(root.second["window_end"])
                      and quantity >= root.second["guard"]):
                    self.end_root(token, "ASK2_SUPPLY_CEILING")

    def trade(self, *, token, side, price, quantity, received, q0, generation,
              input_ref=None, source=None, initial_quantity_ref=None,
              pre_event_best_bid=None, pre_event_best_ask=None):
        leg = "ASK" if side == "BUY" else "BID"
        key = token, leg, price
        root = self.roots.get(token)
        current = self.rounds.get(key)
        if leg == "ASK":
            active = next((r for r in self.rounds.values() if r.token == token and r.side == "ASK"), None)
            anchor = active.price if active else root.reference["price"] if root else pre_event_best_ask
            if price != anchor:
                return
        elif not (current or price == pre_event_best_bid or self.relevant(token, leg, price)):
            return
        point = self.point(token=token, price=price, quantity=quantity, generation=generation,
                           received=received, input_ref=input_ref, source=source, kind="DIRECT_"+side)
        if point is None:
            return
        if (leg == "ASK" and current is not None and current.role == "INELIGIBLE"
                and root is not None and root.second_id is None
                and number(received) > number(root.reference["key"][0])
                and (self.now, *input_ref) > root.reference["key"]):
            # A pre-c1 print cannot seed A2. Nor may its diagnostic flow bucket
            # delay the SOP's first genuinely new post-c1 BUY. Its earlier
            # prints remain raw evidence and are never moved into this A2.
            del self.rounds[key]
            current.path.release()
            self.event("UNASSIGNED_ROUND_ENDED", id=current.id,
                       reason="FIRST_NEW_POST_REFERENCE_BUY_STARTS_ASK2")
            current = None
        # A print at the preceding round's exclusive endpoint starts a physical
        # next round even while the prior watermark has not crossed its end.
        # Keep both sufficient-statistic paths; never relabel this print later.
        for old in tuple(self.closing_rounds.values()):
            if (old.token, old.side, old.price) == key:
                old.path.observe(point)
        if current is not None and number(received) < current.path.end:
            current.path.observe(point)
            if current.path.failure:
                self.invalidate(token, current.path.failure)
            return
        if current is not None:
            current.path.observe(point)
            if current.path.failure:
                self.invalidate(token, current.path.failure)
                return
            self.closing_rounds[current.id] = current
            del self.rounds[key]
        closed = self.closed_windows.get(key)
        if closed and number(received) < closed[0]:
            self.invalidate(token, "LATE_TRADE_TO_CLOSED_ROUND")
            return
        if (q0 <= 0 or initial_quantity_ref is None
                or number(initial_quantity_ref[0]) > number(received)
                or number(initial_quantity_ref[1]) >= number(source)
                or tuple(initial_quantity_ref[2]) >= tuple(input_ref)):
            self.invalidate(token, "PRE_HIT_LEVEL_ORDER_UNPROVEN")
            return
        if len(self.rounds) + len(self.closing_rounds) >= 512:
            raise RuntimeError("SOP_ROUND_CAPACITY")
        self.serial += 1
        identity = f"M19:{generation}:{token}:{leg}:{self.serial}"
        start_key = self.now, *input_ref
        role = "INELIGIBLE"
        after_close = closed is None or number(received) > number(closed[1])
        if leg == "BID":
            role = "BID_CANDIDATE" if price == pre_event_best_bid else "BID_FOLLOWUP_ONLY"
        elif root is not None:
            if root.second is not None or root.second_id is not None:
                role = "ASK_FOLLOWUP"
            elif (after_close and number(received) > number(root.reference["key"][0])
                  and start_key > root.reference["key"]):
                role, root.second_id = "ASK_SECOND", identity
        elif after_close and not any(r.token == token and r.side == "ASK" for r in self.closing_rounds.values()):
            ended = self.root_ends.get(token)
            if ended is None or (number(received) > number(ended[0]) and start_key > ended):
                role = "ASK_REFERENCE"
        path = AnchorRoundPath(token=token, side=leg, price=price, generation=generation,
                               start=received, q0=q0, round_seconds=self.p.round_seconds)
        current = ObservedRound(identity, token, price, generation, start_key, path, role, q0, side=leg)
        self.rounds[key] = current
        path.observe(point)
        self.event("ROUND_STARTED", id=identity, token=token, side=leg, price=price,
                   generation=generation, received=received, q0=q0, role=role,
                   raw_input_ref=input_ref, pre_event_best=pre_event_best_ask if leg == "ASK" else pre_event_best_bid)

    def second(self, root, fact):
        first = root.reference
        if (not fact["data_proven"] or not fact["qualified"]
                or number(self.now) >= number(first["key"][0]) + number(self.p.ask_gap)):
            self.end_root(fact["token"], "FIRST_ASK2_FAILED_OR_EXPIRED_OR_UNKNOWN")
            return
        ratios = {"initial_depth":fact["q0"]/first["q0"], "absolute_hit":fact["hits"]/first["hits"],
                  "normalized_hit":fact["u"]/first["u"], "absolute_loss":fact["loss"]/first["loss"],
                  "normalized_loss":fact["d"]/first["d"]}
        minimum = max(D(".5"), D(".0001")*fact["q0"])
        loss_low = max(minimum, self.p.absolute_loss_ratio_min*first["loss"],
                       self.p.normalized_loss_ratio_min*first["d"]*fact["q0"])
        hit_high = min(self.p.absolute_hit_ratio_max*first["hits"],
                       self.p.normalized_hit_ratio_max*first["u"]*fact["q0"])
        hit_low = max(minimum, self.p.absolute_hit_ratio_min*first["hits"],
                      self.p.normalized_hit_ratio_min*first["u"]*fact["q0"], fact["loss"])
        checks = {
            "nonempty_quantity_intervals":loss_low<=hit_high and loss_low<fact["q0"] and hit_low<=hit_high,
            "initial_depth":self.p.initial_depth_ratio_min<=ratios["initial_depth"]<=self.p.initial_depth_ratio_max,
            "absolute_hit":self.p.absolute_hit_ratio_min<=ratios["absolute_hit"]<=self.p.absolute_hit_ratio_max,
            "normalized_hit":self.p.normalized_hit_ratio_min<=ratios["normalized_hit"]<=self.p.normalized_hit_ratio_max,
            "absolute_loss":ratios["absolute_loss"]>=self.p.absolute_loss_ratio_min,
            "normalized_loss":ratios["normalized_loss"]>=self.p.normalized_loss_ratio_min,
            "hit_at_least_loss":fact["hits"]>=fact["loss"],
            "recovery_not_improved":fact["r"]<=min(self.p.second_recovery_max, first["r"])}
        self.event("ASK_RESPONSE_PAIR_REVIEW", root_id=first["id"], second_id=fact["id"],
                   ratios=ratios, checks=checks, loss_interval=[loss_low,hit_high], hit_interval=[hit_low,hit_high])
        if not all(checks.values()):
            self.end_root(fact["token"], "EMPTY_QUANTITY_INTERVAL" if not checks["nonempty_quantity_intervals"]
                          else "FIRST_ASK2_COMPARISON_FAILED")
        elif (fact["post_window_missing"] or root.current_quantity<=0
              or root.current_quantity>=fact["guard"]
              or (fact["post_window_max"] is not None and fact["post_window_max"]>=fact["guard"])):
            self.end_root(fact["token"], "ASK2_POST_WINDOW_SUPPLY_CEILING")
        else:
            root.second = fact
            self.event("ASK2_FORMED", root_id=first["id"], second_id=fact["id"], **{"component":fact})

    def close(self, watermark, covered):
        for current in self.all_rounds():
            key = current.token, current.side, current.price
            if number(watermark) <= current.path.end:
                continue
            if self.rounds.get(key) is current:
                del self.rounds[key]
            self.closing_rounds.pop(current.id, None)
            result = current.path.finish(available=self.now, watermark=watermark,
                coverage_proven=covered(current.token,float(current.path.start),float(current.path.end)))
            self.closed_windows[key] = current.path.end, self.now
            fact = {**result,"id":current.id,"role":current.role,"key":self.boundary_key(),
                    "window_end":float(current.path.end),"start_key":current.start_key,
                    "u":result["hits"]/result["q0"],"d":result["loss"]/result["q0"],
                    "r":result["recovery"],"guard":result["qmin"]+(D(".40") if current.side=="BID" else D(".50"))*result["loss"],
                    "deadline":float(number(self.now)+number(self.p.ttl)),"current_quantity":current.current_quantity}
            current.path.release()
            self.event("ROUND_CLOSED", **fact)
            root = self.roots.get(current.token)
            if not result["data_proven"]:
                self.event("ROUND_UNKNOWN", id=current.id, side=current.side, reason=result["reason"])
            if current.side == "BID":
                for identity, component in tuple(self.components.items()):
                    if ((component["token"],component["price"]) == (current.token,current.price)
                            and (not result["data_proven"] or (result["qualified"] and not result["stable"]))):
                        self.end_component(identity,"SUBSEQUENT_BID_UNKNOWN_OR_NOT_STABLE")
                if (current.role=="BID_CANDIDATE" and result["status"]=="ROUND_PROVEN"
                        and not fact["post_window_missing"] and current.current_quantity>=fact["guard"]
                        and (fact["post_window_min"] is None or fact["post_window_min"]>=fact["guard"])):
                    self.components[current.id] = fact
                    self.pending_bids.add(current.id)
                continue
            if root is not None:
                if root.second_id == current.id:
                    self.second(root,fact)
                elif root.second is not None and (not result["data_proven"] or (result["qualified"] and result["stable"])):
                    self.end_root(current.token,"SUBSEQUENT_ASK_UNKNOWN_OR_STRICT_STABLE")
                continue
            ended = self.root_ends.get(current.token)
            if current.role!="ASK_REFERENCE" or (ended and current.start_key<=ended):
                continue
            checks = {"quantity_proven_and_qualified":result["data_proven"] and result["qualified"],
                "reference_hit":fact["hits"]>=max(self.p.reference_hit_shares_min,self.p.reference_hit_fraction_min*fact["q0"]),
                "reference_loss":fact["d"]>=self.p.reference_loss_fraction_min,
                "hit_at_least_loss":fact["hits"]>=fact["loss"],
                "original_level_retained":not fact["post_window_missing"] and current.current_quantity>0}
            self.event("ASK_REFERENCE_REVIEW", id=current.id, checks=checks)
            if all(checks.values()):
                self.roots[current.token] = AskRoot(fact,current.current_quantity)
                self.event("ASK_REFERENCE_CONFIRMED", **fact, historical_only=True,
                           old_weak_supply_ceiling_not_active=True)
        if len(self.components)+len(self.roots)>512:
            raise RuntimeError("SOP_COMPONENT_CAPACITY")

    def publish(self):
        for identity in tuple(self.pending_bids):
            component = self.components.get(identity)
            self.pending_bids.discard(identity)
            if component is None:
                continue
            if self.now != component["key"][0]:
                self.end_component(identity,"BID_PUBLICATION_BOUNDARY_LOST")
                continue
            component["key"] = self.key
            self.event("COMPONENT_CONFIRMED", **component)
        for token, root in tuple(self.roots.items()):
            if root.second is None or root.signal_id:
                continue
            second = root.second
            eligible = sorted((c for c in self.components.values()
                if c["token"]==token and c["generation"]==second["generation"]
                and c["price"]<second["price"] and c["current_quantity"]>=c["guard"]
                and abs(number(c["key"][0])-number(second["key"][0]))<number(self.p.pair_gap)
                and number(self.now)<number(c["deadline"])),key=lambda c:c["key"])
            if not eligible:
                continue
            bid = eligible[0]
            deadline = min(bid["deadline"],second["deadline"])
            if (self.now != max(bid["key"][0],second["key"][0]) or number(self.now)>=number(deadline)
                    or root.current_quantity<=0 or root.current_quantity>=second["guard"]):
                self.end_root(token,"PUBLICATION_BOUNDARY_OR_GUARD")
                continue
            identity = "M19:pair:"+root.reference["id"]
            signal = {"id":identity,"token":token,"root_id":root.reference["id"],"version":self.version,
                "component_ids":[bid["id"],root.reference["id"],second["id"]],"confirmed":self.now,
                "deadline":deadline,"anchor_price":bid["price"],"generation":bid["generation"],
                "bid":dict(bid),"ask":dict(second),"prior_reference":dict(root.reference),
                "quality_status":"PROVEN","post_window_prepublication_guard_status":"PROVEN"}
            root.signal_id = identity
            self.signals[identity] = signal
            self.event("SIGNAL_CONFIRMED", **signal)

    def release(self):
        for current in self.all_rounds():
            current.path.release()
        super().release()
        for values in (self.roots,self.root_ends,self.closed_windows,self.closing_rounds,self.pending_bids):
            values.clear()
