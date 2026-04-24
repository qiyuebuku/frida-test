"""L1b 规则集"""
from src.domain.extraction.services.l1b.rules.fund_flow_rules import ALL_FUND_FLOW_RULES
from src.domain.extraction.services.l1b.rules.macro_rules import ALL_MACRO_RULES
from src.domain.extraction.services.l1b.rules.sentiment_rules import ALL_SENTIMENT_RULES
from src.domain.extraction.services.l1b.rules.market_rules import ALL_MARKET_RULES

ALL_RULES = ALL_FUND_FLOW_RULES + ALL_MACRO_RULES + ALL_SENTIMENT_RULES + ALL_MARKET_RULES
