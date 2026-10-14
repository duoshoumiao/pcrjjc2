import asyncio  
from collections import defaultdict  
from aiohttp import web  
from nonebot import logger  
from .database.dal import pcr_sqla, PCRBind  
from .query import query_all  
  
APP_GROUP = -1          # PCRBind.group 为 -1 表示该绑定属于 3333 App  
APP_PORT = 8089         # pcrjjc2 对 App 提供服务的端口  
  
_queues: dict[int, list] = defaultdict(list)  
_conds: dict[int, asyncio.Condition] = defaultdict(asyncio.Condition)  
  
  
async def push_to_app(user_id: int, msg: str):  
    """sendNotice 调用：把推送文本放进该 App 用户的队列并唤醒长轮询"""  
    cond = _conds[user_id]  
    async with cond:  
        _queues[user_id].append(msg)  
        cond.notify_all()  
  
  
async def register(request: web.Request):  
    """App 上报绑定：{user_id: int, items: [{platform, pcrid, name}]}  
    幂等：先按 user_id+platform 删除旧绑定再重新插入"""  
    try:  
        body = await request.json()  
        user_id = int(body["user_id"])  
        items = body.get("items", [])  
        for platform in {int(i["platform"]) for i in items}:  
            await pcr_sqla.delete_bind(user_id, platform)  
        for i in items:  
            await pcr_sqla.insert_bind({  
                "user_id": user_id,  
                "pcrid": int(i["pcrid"]),  
                "platform": int(i["platform"]),  
                "name": str(i.get("name") or i["pcrid"]),  
                "group": APP_GROUP,  
            })  
        return web.json_response({"code": 0, "count": len(items)})  
    except Exception as e:  
        logger.error(f"app register failed: {e}")  
        return web.json_response({"code": -1, "msg": str(e)})  
  
  
async def poll(request: web.Request):  
    """App 长轮询：GET /app/poll?uid=x&timeout=30"""  
    user_id = int(request.query.get("uid", "0"))  
    timeout = min(float(request.query.get("timeout", "30")), 60)  
    cond = _conds[user_id]  
    async with cond:  
        try:  
            await asyncio.wait_for(  
                cond.wait_for(lambda: bool(_queues[user_id])), timeout)  
        except asyncio.TimeoutError:  
            pass  
        msgs, _queues[user_id] = _queues[user_id], []  
    return web.json_response({"code": 0, "msgs": msgs})  
  
  
async def unregister(request: web.Request):  
    body = await request.json()  
    user_id = int(body["user_id"])  
    for platform in (0, 1, 2, 3):  
        await pcr_sqla.delete_bind(user_id, platform)  
    return web.json_response({"code": 0})  
  
  
async def app_query(request: web.Request):  
    """App 即时查询：POST {platform: int, pcrids: [int]}  
    走服务端本地账号队列 query_all，返回完整 /profile/get_profile 的 res 列表"""  
    try:  
        body = await request.json()  
        platform = int(body["platform"])  
        pcrids = [int(p) for p in body["pcrids"]]  
        binds = [PCRBind(user_id=0, pcrid=p, platform=platform,  
                         name=str(p), group=APP_GROUP) for p in pcrids]  
        results = []  
        done = asyncio.Event()  
        storage: dict = {}  
  
        async def cb(st):  
            results.append(st["res"])  
            if len(results) >= len(binds):  
                done.set()  
  
        await query_all(binds, platform, cb, storage, priority=0)  
        try:  
            await asyncio.wait_for(done.wait(), timeout=60)  
        except asyncio.TimeoutError:  
            pass  
        return web.json_response({"code": 0, "results": results})  
    except Exception as e:  
        logger.error(f"app query failed: {e}")  
        return web.json_response({"code": -1, "msg": str(e)})  
  
  
async def start_app_server():  
    app = web.Application()  
    app.router.add_post("/app/register", register)  
    app.router.add_get("/app/poll", poll)  
    app.router.add_post("/app/unregister", unregister)  
    app.router.add_post("/app/query", app_query)  
    runner = web.AppRunner(app)  
    await runner.setup()  
    await web.TCPSite(runner, "0.0.0.0", APP_PORT).start()  
    logger.info(f"pcrjjc2 app push server listening on :{APP_PORT}")