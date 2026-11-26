import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

# psycopg 异步模式不支持 Windows 默认的 ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
