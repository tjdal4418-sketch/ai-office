from app.agents.bookkeeping import BookkeepingAgent
from app.agents.corptax import CorpTaxAgent
from app.agents.manager import ManagerAgent
from app.agents.payroll import PayrollAgent
from app.agents.vat import VatAgent

# 사무실 배치 순서: 매니저 → 실무 4명
REGISTRY = {a.agent_id: a for a in (ManagerAgent(), PayrollAgent(), BookkeepingAgent(), VatAgent(), CorpTaxAgent())}
