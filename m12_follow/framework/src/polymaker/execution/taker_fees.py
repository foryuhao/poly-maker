"""Shared fee identity and collateral bounds extracted from the tested CA adapter.

No strategy lifecycle, account state, transport or execution authority lives here.
"""
from decimal import Decimal

MICRO = Decimal('0.000001')


def fee_parameters(raw, tokens):
    if ({str(t['t']) for t in raw.get('t', []) if t} != set(tokens)
            or not isinstance(raw.get('fd'), dict)):
        raise ValueError('CA_TAKER_FEE_PARAMETERS_UNAVAILABLE')
    fd = raw['fd']
    rate, exponent = Decimal(str(fd['r'])), Decimal(str(fd['e']))
    if (not rate.is_finite() or not 0 <= rate < 1 or not exponent.is_finite()
            or exponent != int(exponent) or not 1 <= exponent <= 5 or fd.get('to') is not True):
        raise ValueError('CA_TAKER_FEE_PARAMETERS_UNSUPPORTED')
    return {'rate': str(rate), 'exponent': int(exponent)}


def fee_reserve(quantity, params):
    # p(1-p)<=1/4. Doubling covers positive five-decimal per-match rounding.
    return quantity*Decimal(params['rate'])*Decimal('.25')**params['exponent']*2+MICRO


def buy_amount_fee_reserve(amount, params):
    # An immediate BUY is collateral capped and may acquire extra shares at a
    # better price. For exponent>=1, fee per collateral dollar is <=rate.
    return amount*Decimal(params['rate'])*2+MICRO


def buy_fee_reserve(quantity, price, params):
    return buy_amount_fee_reserve(quantity*price, params)
