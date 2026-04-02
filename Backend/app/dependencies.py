from app.core.config import settings
from app.core.security import SecurityManager
from app.services.claude_service import ClaudeService
from app.services.multi_agent_graph import DeepTaskOrchestrator
from app.services.personal_agent import PersonalAgentService
from app.services.websocket_manager import WebSocketManager

security_manager = SecurityManager(settings.encryption_master_key)
ws_manager = WebSocketManager()
claude_service = ClaudeService(settings.claude_api_key)

deep_task_orchestrator = DeepTaskOrchestrator(ws_manager, claude_service)
personal_agent_service = PersonalAgentService(ws_manager, claude_service)

