from app.core.config import settings
from app.core.security import SecurityManager
from app.services.claude_service import ClaudeService
from app.services.context_manager import ContextManager
from app.services.multi_agent_graph import DeepTaskOrchestrator
from app.services.personal_agent import PersonalAgentService
from app.services.websocket_manager import WebSocketManager

security_manager = SecurityManager(settings.encryption_master_key)
ws_manager = WebSocketManager()
context_manager = ContextManager(
    redis_url=settings.redis_url,
    ttl_seconds=settings.chat_context_ttl_seconds,
    max_messages=settings.chat_context_max_messages,
    key_prefix=settings.chat_context_key_prefix,
)
claude_service = ClaudeService(
    api_key=settings.claude_api_key,
    auth_token=settings.anthropic_auth_token,
    base_url=settings.anthropic_base_url or "https://claude.1000.school",
    preferred_model=settings.claude_model,
    openai_api_key=settings.openai_api_key,
    openai_fallback_url=settings.openai_fallback_url,
    openai_fallback_model=settings.openai_fallback_model,
)

deep_task_orchestrator = DeepTaskOrchestrator(ws_manager, claude_service)
personal_agent_service = PersonalAgentService(ws_manager, claude_service)
