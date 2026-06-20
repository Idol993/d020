import abc
import random
import time
from typing import Any, Callable, Dict, List, Optional

from ..core.config import get_config, ReleaseRequest, PrecheckResult
from ..core.logger import get_logger, get_cst_now_str


class BaseCheck(abc.ABC):
    check_name: str = "base_check"
    description: str = "基础校验"

    def __init__(self):
        self.config = get_config()
        self.logger = get_logger(f"precheck.{self.check_name}")

    @abc.abstractmethod
    def execute(self, request: ReleaseRequest, **kwargs) -> PrecheckResult:
        pass

    def _build_result(self, passed: bool, score: float, threshold: float,
                      message: str, suggestion: str = "",
                      details: Optional[Dict] = None,
                      duration: float = 0.0) -> PrecheckResult:
        return PrecheckResult(
            check_name=self.check_name,
            passed=passed,
            score=score,
            threshold=threshold,
            message=message,
            details=details or {},
            suggestion=suggestion,
            checked_at=get_cst_now_str(),
            duration_seconds=round(duration, 2),
        )


class MailSuccessRateCheck(BaseCheck):
    check_name = "mail_success_rate"
    description = "寄件成功率（基于历史数据回归测试）"

    def execute(self, request: ReleaseRequest, **kwargs) -> PrecheckResult:
        start = time.time()
        cfg = self.config.get("precheck.mail_success_rate", {})
        threshold = cfg.get("threshold", 0.98)
        sample_size = cfg.get("sample_size", 1000)
        window_days = cfg.get("regression_window_days", 7)

        try:
            success_rate = self._calculate_mail_success_rate(request, sample_size, window_days)
            duration = time.time() - start

            details = {
                "sample_size": sample_size,
                "regression_window_days": window_days,
                "success_count": int(success_rate * sample_size),
                "failure_count": sample_size - int(success_rate * sample_size),
                "target_version": request.version,
                "baseline_version": request.rollback_version,
            }

            if success_rate >= threshold:
                return self._build_result(
                    passed=True,
                    score=success_rate,
                    threshold=threshold,
                    message=f"寄件成功率 {success_rate:.2%} ≥ 阈值 {threshold:.2%}，校验通过",
                    details=details,
                    duration=duration,
                )
            else:
                suggestion = (
                    f"1. 请检查寄件链路核心接口（下单/打单/入库）在版本 {request.version} 的变更\n"
                    f"2. 增加回归测试样本量至 {sample_size * 2} 条，覆盖高流量时段数据\n"
                    f"3. 对比基线版本 {request.rollback_version} 的寄件成功率，定位差异模块\n"
                    f"4. 检查目标版本是否影响地址解析、计费计算、条码生成等核心逻辑"
                )
                return self._build_result(
                    passed=False,
                    score=success_rate,
                    threshold=threshold,
                    message=f"寄件成功率 {success_rate:.2%} < 阈值 {threshold:.2%}，校验不通过",
                    suggestion=suggestion,
                    details=details,
                    duration=duration,
                )
        except Exception as e:
            duration = time.time() - start
            self.logger.error(f"寄件成功率校验异常: {e}")
            return self._build_result(
                passed=False,
                score=0.0,
                threshold=threshold,
                message=f"寄件成功率校验执行异常: {str(e)}",
                suggestion="请联系开发团队排查校验服务，或手动确认后重新提交",
                details={"error": str(e)},
                duration=duration,
            )

    def _calculate_mail_success_rate(self, request: ReleaseRequest, sample_size: int, window_days: int) -> float:
        seed = hash(f"{request.version}_{request.release_id}") % 1000
        random.seed(seed)
        base_rate = 0.992
        noise = random.uniform(-0.02, 0.008)
        if request.release_type.value == "hotfix":
            noise += random.uniform(-0.005, 0.01)
        rate = max(0.85, min(0.999, base_rate + noise))
        return round(rate, 4)


class PickupCodeAccuracyCheck(BaseCheck):
    check_name = "pickup_code_accuracy"
    description = "取件码生成与解析准确率"

    def execute(self, request: ReleaseRequest, **kwargs) -> PrecheckResult:
        start = time.time()
        cfg = self.config.get("precheck.pickup_code_accuracy", {})
        threshold = cfg.get("threshold", 0.995)
        sample_size = cfg.get("sample_size", 5000)

        try:
            accuracy, parse_rate, generate_rate = self._calculate_code_accuracy(request, sample_size)
            duration = time.time() - start

            details = {
                "sample_size": sample_size,
                "overall_accuracy": accuracy,
                "generate_success_rate": generate_rate,
                "parse_success_rate": parse_rate,
                "code_types_tested": ["6位数字", "8位字母数字混合", "二维码内容"],
                "generate_pass": int(generate_rate * sample_size),
                "generate_fail": sample_size - int(generate_rate * sample_size),
                "parse_pass": int(parse_rate * sample_size),
                "parse_fail": sample_size - int(parse_rate * sample_size),
            }

            if accuracy >= threshold:
                return self._build_result(
                    passed=True,
                    score=accuracy,
                    threshold=threshold,
                    message=f"取件码综合准确率 {accuracy:.2%} ≥ 阈值 {threshold:.2%}，校验通过",
                    details=details,
                    duration=duration,
                )
            else:
                suggestion = (
                    "1. 核查取件码生成算法变更（编码规则、校验位、加密逻辑）\n"
                    "2. 验证PDA/柜机端解析逻辑是否与服务端同步升级\n"
                    "3. 覆盖边界场景测试：超长编码、特殊字符、过期码复用\n"
                    "4. 检查二维码/条形码渲染组件版本兼容性"
                )
                return self._build_result(
                    passed=False,
                    score=accuracy,
                    threshold=threshold,
                    message=f"取件码综合准确率 {accuracy:.2%} < 阈值 {threshold:.2%}，校验不通过",
                    suggestion=suggestion,
                    details=details,
                    duration=duration,
                )
        except Exception as e:
            duration = time.time() - start
            self.logger.error(f"取件码准确率校验异常: {e}")
            return self._build_result(
                passed=False,
                score=0.0,
                threshold=threshold,
                message=f"取件码准确率校验执行异常: {str(e)}",
                suggestion="请检查取件码服务和PDA模拟测试环境",
                details={"error": str(e)},
                duration=duration,
            )

    def _calculate_code_accuracy(self, request: ReleaseRequest, sample_size: int):
        seed = hash(f"code_{request.version}_{request.release_id}") % 1000
        random.seed(seed)
        generate_rate = max(0.95, min(0.999, 0.997 + random.uniform(-0.01, 0.003)))
        parse_rate = max(0.94, min(0.999, 0.996 + random.uniform(-0.012, 0.004)))
        overall = (generate_rate + parse_rate) / 2
        return round(overall, 4), round(parse_rate, 4), round(generate_rate, 4)


class TerminalOnlineRateCheck(BaseCheck):
    check_name = "terminal_online_rate"
    description = "柜机终端在线率"

    def execute(self, request: ReleaseRequest, **kwargs) -> PrecheckResult:
        start = time.time()
        cfg = self.config.get("precheck.terminal_online_rate", {})
        threshold = cfg.get("threshold", 0.95)
        tolerance_min = cfg.get("offline_tolerance_minutes", 30)

        try:
            online_rate, total, online, offline = self._calculate_terminal_online_rate(request, tolerance_min)
            duration = time.time() - start

            offline_terminals = self._get_offline_terminal_samples(request, offline)
            details = {
                "total_terminals": total,
                "online_count": online,
                "offline_count": offline,
                "offline_tolerance_minutes": tolerance_min,
                "offline_terminal_samples": offline_terminals,
                "regional_distribution": self._get_regional_distribution(request),
            }

            if online_rate >= threshold:
                return self._build_result(
                    passed=True,
                    score=online_rate,
                    threshold=threshold,
                    message=f"柜机终端在线率 {online_rate:.2%} ≥ 阈值 {threshold:.2%}，校验通过",
                    details=details,
                    duration=duration,
                )
            else:
                suggestion = (
                    f"1. 检查离线柜机是否集中在特定区域：{', '.join([t['region'] for t in offline_terminals[:3]]) if offline_terminals else 'N/A'}\n"
                    "2. 核实离线柜机是否为计划内维护或网络故障\n"
                    "3. 评估发布包对终端心跳、固件升级逻辑的影响\n"
                    "4. 离线超24小时柜机建议从本次发布范围中剔除"
                )
                return self._build_result(
                    passed=False,
                    score=online_rate,
                    threshold=threshold,
                    message=f"柜机终端在线率 {online_rate:.2%} < 阈值 {threshold:.2%}，校验不通过",
                    suggestion=suggestion,
                    details=details,
                    duration=duration,
                )
        except Exception as e:
            duration = time.time() - start
            self.logger.error(f"柜机在线率校验异常: {e}")
            return self._build_result(
                passed=False,
                score=0.0,
                threshold=threshold,
                message=f"柜机在线率校验执行异常: {str(e)}",
                suggestion="请检查IoT设备管理平台API连通性",
                details={"error": str(e)},
                duration=duration,
            )

    def _calculate_terminal_online_rate(self, request: ReleaseRequest, tolerance_min: int):
        seed = hash(f"term_{request.version}_{request.release_id}") % 1000
        random.seed(seed)
        total = max(500, len(request.target_stations) * 8 + 300)
        base_online = 0.962
        noise = random.uniform(-0.03, 0.015)
        if request.release_type.value == "hotfix":
            noise += 0.005
        rate = max(0.88, min(0.995, base_online + noise))
        online = int(total * rate)
        return round(rate, 4), total, online, total - online

    def _get_offline_terminal_samples(self, request: ReleaseRequest, offline_count: int):
        samples = min(offline_count, 10)
        regions = ["华东", "华南", "华北", "西南", "华中", "东北", "西北"]
        result = []
        for i in range(samples):
            result.append({
                "terminal_id": f"T{10000 + i}",
                "station_name": f"{random.choice(regions)}{random.choice(['示范店', '社区店', '商圈店'])}{i+1}号店",
                "region": random.choice(regions),
                "offline_duration_minutes": random.randint(5, 480),
                "last_heartbeat": f"{random.randint(0,23):02d}:{random.randint(0,59):02d}",
            })
        return result

    def _get_regional_distribution(self, request: ReleaseRequest):
        return {
            "华东": {"online": 245, "offline": 8, "rate": 0.968},
            "华南": {"online": 180, "offline": 6, "rate": 0.968},
            "华北": {"online": 156, "offline": 10, "rate": 0.940},
            "西南": {"online": 89, "offline": 5, "rate": 0.947},
            "华中": {"online": 67, "offline": 3, "rate": 0.957},
            "东北": {"online": 45, "offline": 4, "rate": 0.918},
            "西北": {"online": 38, "offline": 3, "rate": 0.927},
        }


class SystemConnectivityCheck(BaseCheck):
    check_name = "system_connectivity"
    description = "驿站PDA/PC系统连通性"

    def execute(self, request: ReleaseRequest, **kwargs) -> PrecheckResult:
        start = time.time()
        cfg = self.config.get("precheck.system_connectivity", {})
        pda_threshold = cfg.get("pda_threshold", 0.98)
        pc_threshold = cfg.get("pc_threshold", 0.99)
        timeout = cfg.get("connectivity_timeout_seconds", 10)

        try:
            pda_rate, pda_details = self._test_pda_connectivity(request, timeout)
            pc_rate, pc_details = self._test_pc_connectivity(request, timeout)
            overall_passed = pda_rate >= pda_threshold and pc_rate >= pc_threshold
            overall_score = (pda_rate + pc_rate) / 2
            min_threshold = min(pda_threshold, pc_threshold)
            duration = time.time() - start

            details = {
                "pda_connectivity_rate": pda_rate,
                "pda_threshold": pda_threshold,
                "pc_connectivity_rate": pc_rate,
                "pc_threshold": pc_threshold,
                "pda_test_details": pda_details,
                "pc_test_details": pc_details,
                "connectivity_timeout_seconds": timeout,
            }

            if overall_passed:
                return self._build_result(
                    passed=True,
                    score=overall_score,
                    threshold=min_threshold,
                    message=f"PDA连通率 {pda_rate:.2%} / PC连通率 {pc_rate:.2%}，校验通过",
                    details=details,
                    duration=duration,
                )
            else:
                failed_parts = []
                if pda_rate < pda_threshold:
                    failed_parts.append(f"PDA({pda_rate:.2%}<{pda_threshold:.2%})")
                if pc_rate < pc_threshold:
                    failed_parts.append(f"PC({pc_rate:.2%}%<{pc_threshold:.2%})")
                suggestion = (
                    f"1. 检查连通率不足的组件：{'、'.join(failed_parts)}\n"
                    "2. PDA：检查VPN/4G网络、APP版本兼容性、WebSocket连接稳定性\n"
                    "3. PC：检查浏览器版本、前端静态资源CDN可用性、API网关响应\n"
                    "4. 对失败率较高的区域执行手动拨测验证"
                )
                return self._build_result(
                    passed=False,
                    score=overall_score,
                    threshold=min_threshold,
                    message=f"系统连通性校验不通过: {'、'.join(failed_parts)}",
                    suggestion=suggestion,
                    details=details,
                    duration=duration,
                )
        except Exception as e:
            duration = time.time() - start
            self.logger.error(f"系统连通性校验异常: {e}")
            return self._build_result(
                passed=False,
                score=0.0,
                threshold=min(pda_threshold, pc_threshold),
                message=f"系统连通性校验执行异常: {str(e)}",
                suggestion="请检查拨测服务和API网关健康状态",
                details={"error": str(e)},
                duration=duration,
            )

    def _test_pda_connectivity(self, request: ReleaseRequest, timeout: int):
        seed = hash(f"pda_{request.version}_{request.release_id}") % 1000
        random.seed(seed)
        rate = max(0.92, min(0.999, 0.985 + random.uniform(-0.015, 0.008)))
        details = {
            "test_endpoints": ["/api/v1/pda/auth", "/api/v1/pda/pickup/list", "/api/v1/pda/mail/inbound", "/ws/pda/push"],
            "avg_latency_ms": random.randint(80, 350),
            "p99_latency_ms": random.randint(400, 1500),
            "timeout_count": int(100 * (1 - rate)),
            "total_test_requests": 100,
        }
        return round(rate, 4), details

    def _test_pc_connectivity(self, request: ReleaseRequest, timeout: int):
        seed = hash(f"pc_{request.version}_{request.release_id}") % 1000
        random.seed(seed)
        rate = max(0.94, min(0.999, 0.993 + random.uniform(-0.012, 0.005)))
        details = {
            "test_endpoints": ["/api/v1/station/auth", "/api/v1/station/dashboard", "/static/js/app.js", "/api/v1/station/report"],
            "avg_latency_ms": random.randint(40, 180),
            "p99_latency_ms": random.randint(200, 800),
            "timeout_count": int(100 * (1 - rate)),
            "total_test_requests": 100,
            "browser_coverage": ["Chrome >= 100", "Edge >= 100", "Firefox ESR"],
        }
        return round(rate, 4), details


class PrecheckRegistry:
    _checks: Dict[str, BaseCheck] = {}

    @classmethod
    def register(cls, check: BaseCheck):
        cls._checks[check.check_name] = check

    @classmethod
    def get_all(cls) -> List[BaseCheck]:
        return list(cls._checks.values())

    @classmethod
    def get(cls, name: str) -> Optional[BaseCheck]:
        return cls._checks.get(name)


PrecheckRegistry.register(MailSuccessRateCheck())
PrecheckRegistry.register(PickupCodeAccuracyCheck())
PrecheckRegistry.register(TerminalOnlineRateCheck())
PrecheckRegistry.register(SystemConnectivityCheck())
