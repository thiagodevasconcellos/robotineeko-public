from pydantic import BaseModel, ConfigDict, Field


class TradeRuntimeSleevePayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str = ''
    label: str = ''
    enabled: bool = True
    symbol: str = 'EURUSD'
    timeframe: str = 'M1'
    volume: float = 0.01
    volumeMode: str = 'fixed_volume'
    fixedVolume: float | None = None
    baseVolume: float | None = None
    maxVolumeCap: float | None = None
    referenceCapital: float | None = None
    portfolioId: str = ''
    portfolioLabel: str = ''
    pipelineId: str = ''
    pipelineLabel: str = ''
    sourceStrategyId: str = ''
    strategy: dict | None = None
    indicators: list[dict] = Field(default_factory=list)


class TradeRuntimePipelinePayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str = ''
    label: str = ''
    enabled: bool = True
    portfolioMode: str = 'parallel_sleeves'
    sleeves: list[TradeRuntimeSleevePayload] = Field(default_factory=list)


class TradeRuntimePortfolioPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str = ''
    label: str = ''
    enabled: bool = True
    capitalMode: str = 'legacy_shared'
    capitalValue: float | None = None
    rebalanceMode: str = 'static'
    pipelines: list[TradeRuntimePipelinePayload] = Field(default_factory=list)


class TradeRuntimeConfigureRequest(BaseModel):
    model_config = ConfigDict(extra='allow')

    mode: str = 'parallel_sleeves'
    executionMode: str = 'paper'
    brokerProfileId: str = ''
    brokerProfileLabel: str = ''
    sameSymbolExecutionPolicy: str = 'independent'
    signalValiditySeconds: int = 10
    latencyBudgetMs: int = 150
    liveDispatchArmed: bool = False
    portfolioStructureVersion: int | None = None
    portfolios: list[TradeRuntimePortfolioPayload] = Field(default_factory=list)
    sleeves: list[TradeRuntimeSleevePayload] = Field(default_factory=list)
