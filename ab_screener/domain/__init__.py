"""领域模型与纯业务逻辑。

入场定义：
- v1：`entry_definition`（A_POOL_STRICT_NEXT_OPEN_V1）——生产候选，冻结语义。
- v2：`entry_definition_v2`（A_POOL_STRICT_NEXT_OPEN_V2）——研究候选，只读。
- 解析与指纹：`entry_registry`（所有消费者经 registry 显式解析，报告携带 semantic hash）。
"""
