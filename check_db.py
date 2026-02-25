import asyncio
from sqlalchemy import select
from src.app.database import async_session_maker
from src.app.api.models import Location

async def main():
    async with async_session_maker() as session:
        result = await session.execute(
            select(Location.name, Location.retell_agent_id)
            .where(Location.retell_agent_id.isnot(None))
        )
        for row in result:
            print(f"Name: {row.name}, Agent: {row.retell_agent_id[:50]}...")

asyncio.run(main())
