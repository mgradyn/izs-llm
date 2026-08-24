import asyncio
from core.tools.catalog_lookup import search_helper_functions

async def main():
    print(search_helper_functions("getAssembly"))
    print(search_helper_functions("input"))

asyncio.run(main())
