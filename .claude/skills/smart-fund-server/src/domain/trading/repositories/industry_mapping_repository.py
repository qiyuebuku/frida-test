"""行业映射仓储抽象接口"""
from abc import ABC, abstractmethod


class IndustryMappingRepository(ABC):
    """ft_industry_mapping + ft_industry_index + ft_index_fund 联合查询"""

    @abstractmethod
    def resolve_industry_to_funds(self, industry_name: str) -> list[dict]:
        """industry_name → 候选基金列表(按 liquidity_score desc, fee_rate asc 排序)

        匹配规则: industry_name 优先精确,然后 aliases,最后 keywords。

        Returns: [{
            fund_code, fund_name, fund_type, fee_rate, liquidity_score,
            index_code, index_name, weight,
            industry_priority,
        }]
        """
        ...

    @abstractmethod
    def fund_to_industry_keywords(self, fund_code: str) -> list[str]:
        """fund_code 反查它对应的所有行业关键词(name + aliases + keywords 并集)"""
        ...
