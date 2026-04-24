"""阈值计算器 — 从历史数据滚动计算规则阈值"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from src.domain.extraction.services.l1b.rules import ALL_RULES
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.extraction import RuleThreshold

logger = logging.getLogger(__name__)

# 冷启动默认阈值（数据不足 90 天时使用）
DEFAULT_THRESHOLDS = {
    "northbound_large_inflow": {"percentile_95": 100.0, "threshold_config": {"percentile_5": -100.0}},
    "sector_fund_abnormal": {"percentile_95": 50.0, "threshold_config": {"percentile_5": -50.0}},
    "dragon_tiger_concentrated": {"percentile_95": 5000.0},
    "cpi_surprise": {"sigma_value": 0.3, "threshold_config": {"n_sigma": 0.5}},
    "pmi_cross_50": {"percentile_95": 55.0},
    "rate_change": {},
    "limit_pool_surge": {"percentile_95": 80.0},
    "limit_up_surge": {"percentile_95": 80.0},
    "limit_down_surge": {"percentile_95": 30.0},
}


class ThresholdCalculator:

    def refresh_all(self) -> int:
        """遍历所有规则，计算阈值并写入 ft_rule_thresholds

        Returns: 刷新的规则数量
        """
        refreshed = 0
        for rule in ALL_RULES:
            try:
                thresholds = self._calculate_for_rule(rule)
                if thresholds:
                    self._save_threshold(rule, thresholds)
                    refreshed += 1
                else:
                    # 使用冷启动默认值
                    defaults = DEFAULT_THRESHOLDS.get(rule.rule_name, {})
                    if defaults:
                        self._save_threshold(rule, defaults, is_default=True)
                        refreshed += 1
            except Exception as e:
                logger.warning(f"[threshold] 计算 {rule.rule_name} 失败: {e}")
        logger.info(f"[threshold] 刷新完成: {refreshed}/{len(ALL_RULES)}")
        return refreshed

    def _calculate_for_rule(self, rule) -> dict | None:
        """计算单条规则的阈值"""
        source = rule.data_source
        rule_name = rule.rule_name

        if source == "ft_market_flow":
            return self._calc_market_flow(rule)
        elif source == "ft_macro_indicators":
            return self._calc_macro_indicators(rule)
        elif source == "ft_sentiment":
            return self._calc_sentiment(rule)
        elif source == "ft_market_cache":
            return self._calc_market_cache(rule)
        return None

    def _calc_market_flow(self, rule) -> dict | None:
        """从 ft_market_flow 计算阈值"""
        data_type_map = {
            "northbound_large_inflow": "northbound",
            "sector_fund_abnormal": "sector_flow",
            "dragon_tiger_concentrated": "dragon_tiger",
        }
        data_type = data_type_map.get(rule.rule_name)
        if not data_type:
            return None

        field_map = {
            "northbound_large_inflow": "net_flow",
            "sector_fund_abnormal": "net_amount",
            "dragon_tiger_concentrated": "net_amt",
        }
        field = field_map.get(rule.rule_name, "value")

        sql = text(f"""
            SELECT
                percentile_cont(0.95) WITHIN GROUP (ORDER BY (data->>'{field}')::float) as p95,
                percentile_cont(0.05) WITHIN GROUP (ORDER BY (data->>'{field}')::float) as p5,
                stddev_pop((data->>'{field}')::float) as sigma
            FROM ft_market_flow
            WHERE data_type = :dt AND trade_date >= :cutoff
        """)
        return self._exec_percentile(sql, {"dt": data_type, "cutoff": self._cutoff_date()})

    def _calc_macro_indicators(self, rule) -> dict | None:
        """从 ft_macro_indicators 计算阈值"""
        indicator_map = {
            "cpi_surprise": "cpi",
            "pmi_cross_50": "pmi",
            "rate_change": "lpr_1y",  # 代表性利率
        }
        indicator = indicator_map.get(rule.rule_name)
        if not indicator:
            return None

        sql = text("""
            SELECT
                percentile_cont(0.95) WITHIN GROUP (ORDER BY value) as p95,
                percentile_cont(0.05) WITHIN GROUP (ORDER BY value) as p5,
                stddev_pop(value) as sigma
            FROM ft_macro_indicators
            WHERE indicator = :ind AND published_at >= :cutoff
        """)
        return self._exec_percentile(sql, {"ind": indicator, "cutoff": self._cutoff_date()})

    def _calc_sentiment(self, rule) -> dict | None:
        """从 ft_sentiment 计算阈值"""
        if rule.rule_name == "limit_pool_surge":
            # 从 limit_pool 数据提取 total
            sql = text("""
                SELECT
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY (data->>'total')::int) as p95,
                    percentile_cont(0.05) WITHIN GROUP (ORDER BY (data->>'total')::int) as p5,
                    stddev_pop((data->>'total')::float) as sigma
                FROM ft_sentiment
                WHERE data_type = 'limit_pool' AND trade_date >= :cutoff
            """)
            return self._exec_percentile(sql, {"cutoff": self._cutoff_date()})
        return None

    def _calc_market_cache(self, rule) -> dict | None:
        """从 ft_market_cache 计算阈值"""
        if rule.rule_name == "limit_up_surge":
            return self._calc_cache_field("market_overview", "limit_up.total")
        elif rule.rule_name == "limit_down_surge":
            return self._calc_cache_field("market_overview", "limit_down.total")
        return None

    def _calc_cache_field(self, data_type: str, json_path: str) -> dict | None:
        """从 ft_market_cache 提取嵌套 JSONB 字段计算阈值"""
        parts = json_path.split(".")
        # 构建 data->'limit_up'->>'total' 路径
        path_expr = "data"
        for p in parts[:-1]:
            path_expr += f"->'{p}'"
        path_expr += f"->>'{parts[-1]}'"

        sql = text(f"""
            SELECT
                percentile_cont(0.95) WITHIN GROUP (ORDER BY ({path_expr})::float) as p95,
                percentile_cont(0.05) WITHIN GROUP (ORDER BY ({path_expr})::float) as p5,
                stddev_pop(({path_expr})::float) as sigma
            FROM ft_market_cache
            WHERE data_type = :dt AND created_at >= :cutoff
        """)
        return self._exec_percentile(sql, {"dt": data_type, "cutoff": self._cutoff_date()})

    @staticmethod
    def _exec_percentile(sql, params: dict) -> dict | None:
        """执行百分位查询并返回结果"""
        try:
            with get_session() as s:
                row = s.execute(sql, params).mappings().first()
                if row is None:
                    return None
                p95 = row.get("p95")
                p5 = row.get("p5")
                sigma = row.get("sigma")
                if p95 is None and sigma is None:
                    return None
                result = {}
                if p95 is not None:
                    result["percentile_95"] = float(p95)
                if p5 is not None:
                    result["threshold_config"] = {"percentile_5": float(p5)}
                if sigma is not None:
                    result["sigma_value"] = float(sigma)
                return result if result else None
        except Exception as e:
            logger.warning(f"percentile 查询失败: {e}")
            return None

    @staticmethod
    def _cutoff_date():
        return (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")

    @staticmethod
    def _save_threshold(rule, thresholds: dict, is_default: bool = False):
        """写入 ft_rule_thresholds"""
        from src.infrastructure.persistence.repositories import RuleThresholdRepositoryImpl
        repo = RuleThresholdRepositoryImpl()

        # 提取 metric_name
        metric = getattr(rule, "metric_field", "") or ""
        if hasattr(rule, "actual_field"):
            metric = rule.actual_field or metric

        data = {
            "rule_name": rule.rule_name,
            "data_source": rule.data_source,
            "metric_name": metric,
            "window_days": 90,
            "percentile_95": thresholds.get("percentile_95"),
            "percentile_99": thresholds.get("percentile_99"),
            "sigma_value": thresholds.get("sigma_value"),
            "threshold_config": thresholds.get("threshold_config", {}),
        }
        if is_default:
            data["last_computed_at"] = None
        repo.upsert_threshold(data)
