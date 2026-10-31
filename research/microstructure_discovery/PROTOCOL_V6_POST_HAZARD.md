# V6 post-count information calibration

2026-10-31 UTC12:48, before computing historical Jan-Aug survival outcomes.
Existing September/October UTC10 six-hour check had1 zero interval/32 quiet
anchors, too uncertain. Current exact tracker count139, bracket115-139 is
therefore a no-further-post event, not a generally stable count forecast.

Independent Jan-Aug 2026 data: all days except first of each month (history
lookback), anchor UTC13, next3h until UTC16, conditional already silent>=3h.
This is fixed using current market time and user information, NOT chosen using
Jan-Aug outcomes. Report UTC10 six-hour benchmark as separately labelled
replication, with multiplicity adjustment for two comparisons. Missing months
and API coverage/pagination ambiguity are exclusions, not zero-post days.
Verify response complete against contiguous monthly chunks or existing tracker
counts before claiming historical coverage. Dedup post ids; count unique ids,
not timestamps (different posts can share a second). Store only identifiers,
created/imported timestamps and source content hashes. Historical feed can be
revised, imported late or filtered differently from exact market contract.

Use 97.5% one-sided exact binomial upper bound for zero-post probability for
each of the two fixed comparisons. Show month-specific and weekday stability.
IID binomial is conditional sensitivity, not unquestioned ground truth; months
and consecutive days can be dependent and posting regimes change. No edge
claim without contract/count matching, current executable depth/fees, and a
credible stationarity/validation argument. A single current event's realized
profit is not evidence of a repeatable forecasting advantage. No orders.
