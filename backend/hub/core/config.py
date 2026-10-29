# -*- coding: utf-8 -*-
"""
全局配置中心（config.py）
========================
作用：把整个项目所有"开关、参数、连接信息"集中到一个地方管理。
好处：以后要改数据库地址、改模型名、改阈值，只改这一个文件，不用满项目找。

怎么用：
    from hub.core.config import Settings   # 导入配置类
    s = Settings()                          # 创建配置实例（会自动读 .env 和环境变量）
    s.app.env                               # 取"当前环境"，得到 "dev"
    s.dqe.spam_threshold                    # 取"水军阈值"，得到 0.75

配置的优先级（谁大听谁的）：
    环境变量(如 HUB_APP__ENV=prod) > .env 文件 > 代码里的默认值
"""

from pathlib import Path

# pydantic：定义"数据模型"的库。BaseModel 是它的基类，
# 我们用 BaseModel 来定义"一组相关的设置字段"（比如 LLM 的设置放一组）。
from pydantic import BaseModel

# pydantic-settings：在 pydantic 基础上，额外支持"从环境变量/.env 读配置"。
# BaseSettings 是它的基类（能力比 BaseModel 多：能读环境变量）。
# SettingsConfigDict 用来配置"环境变量怎么映射到字段"。
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 的绝对路径：backend/.env
# 为什么不用相对路径 "./.env"：相对路径依赖"当前工作目录"，
# 从别的目录跑脚本（比如 python tests/xxx.py）就找不到了。
# 用 __file__ 推算绝对路径，从哪跑都能找到。
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class AppConf(BaseModel):
    """应用通用设置"""
    env: str = "dev"          # 当前环境：dev(开发) / staging(测试) / prod(生产)
    out_dir: str = "out"      # 结果输出目录名（相对 backend/）


class StorageConf(BaseModel):
    """数据库连接设置"""
    url: str = "sqlite:///hub.db"
    # 默认用 SQLite（本地单文件数据库，零配置）。
    # 以后要上生产，改这一行为 postgresql+psycopg://user:pass@host/db 即可，
    # 其余代码完全不用动——这就是"把连接信息抽出来"的意义。


class LLMConf(BaseModel):
    """文本大模型设置（M3 分类/归因、M4 簇名、M5/M6 决策）"""
    base_url: str = ""        # LLM 服务的地址（OpenAI 兼容接口的域名）
    model: str = ""           # 模型名，如 "qwen-plus" / "gpt-4o-mini"
    api_key: str = ""         # 密钥（千万别写死，从 .env 读）
    temperature: float = 0.0  # 温度：0 表示输出最稳定（抽取/分类用 0）
    timeout_s: int = 60       # 单次调用超时（秒）
    max_repair_retries: int = 2   # 结构化输出失败时，最多修复重试几次


class VisionConf(BaseModel):
    """视觉模型设置（M2 图片打标）。

    为什么要和文本模型分开：
        文本任务用纯文本模型更强（VLM 为了视觉牺牲了语言能力），
        图片任务必须用 VLM。**一个模型名管两类任务，必然打架**——
        实测踩过：`.env` 里同一个 `HUB_LLM__MODEL` 写了两行，
        后一行覆盖前一行，谁生效纯靠顺序。

        base_url 留空时回落到 LLMConf.base_url（同一个 Ollama 实例）。
    """
    base_url: str = ""        # 留空 = 用 HUB_LLM__BASE_URL
    model: str = "qwen2.5vl:3b"
    timeout_s: int = 180      # 图片比文本慢，超时给长一点


class DqeConf(BaseModel):
    """数据降噪引擎（DQE）设置"""
    engine: str = "hybrid"    # 用哪套引擎：hybrid(规则+ML) / ml / rule
    spam_threshold: float = 0.75        # 水军判定阈值：spam_score 高于它就算水军
    spam_threshold_negative: float = 0.90  # 差评的水军阈值（更高，防止误杀差评）
    anomaly_threshold: float = 0.72     # 评分异常判定阈值
    model_dir: str = "models/dqe"       # ML 模型文件存放目录


class GatewayConf(BaseModel):
    """工具网关设置"""
    deny_by_default: bool = True        # 默认拒绝：未注册的工具一律不让执行（安全底线）
    auto_execute_max_risk: str = "L1"   # 自动执行的风险等级上限：L1 及以下自动，L2+ 要人工


class Settings(BaseSettings):
    """
    总配置：把上面几个"分组"合并成一个总的 Settings 对象。
    业务代码只 import 这一个 Settings 就够了。
    """

    # model_config：告诉 pydantic-settings 怎么从环境变量读配置
    model_config = SettingsConfigDict(
        env_prefix="HUB_",              # 环境变量统一加 HUB_ 前缀
        env_nested_delimiter="__",      # 嵌套用双下划线：HUB_APP__ENV 对应 app.env
        env_file=str(_ENV_FILE),        # .env 的绝对路径（不依赖当前工作目录）
        env_file_encoding="utf-8",      # .env 用 UTF-8 编码
        extra="ignore",                 # 遇到不认识的配置项，忽略而不是报错
    )

    # 6 个配置分组，都给了默认值，所以"啥都不配也能跑起来"
    app: AppConf = AppConf()
    storage: StorageConf = StorageConf()
    llm: LLMConf = LLMConf()          # 文本模型
    vision: VisionConf = VisionConf()  # 视觉模型（M2）
    dqe: DqeConf = DqeConf()
    gateway: GatewayConf = GatewayConf()
