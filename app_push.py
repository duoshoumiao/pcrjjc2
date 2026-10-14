import asyncio  
import itertools  
from collections import defaultdict  
from aiohttp import web  
from nonebot import logger  
from .database.dal import pcr_sqla, PCRBind  
  
APP_GROUP = -1          # PCRBind.group 为 -1 表示该绑定属于 3333 App  
APP_PORT = 8089         # pcrjjc2 对 App 提供服务的端口  
  
_queues: dict[int, list] = defaultdict(list)  
_conds: dict[int, asyncio.Condition] = defaultdict(asyncio.Condition)  
_qcounter = itertools.count()   # 查询队列元组第二位的单调递增计数器，防 TypeError  
  
  
async def push_to_app(user_id: int, payload):  
    """sendNotice 调用：把推送数据放进该 App 用户的队列并唤醒长轮询。  
    payload 可以是 str（旧文本）或 dict（结构化，含击剑记录字段）"""  
    cond = _conds[user_id]  
    async with cond:  
        _queues[user_id].append(payload)  
        cond.notify_all()  
  
  
async def register(request: web.Request):  
    """App 上报绑定：{user_id: int, items: [{platform, pcrid, name,  
    jjc_notice, pjjc_notice, up_notice, online_notice}]}  
    幂等：先按 user_id 全平台删除旧绑定再重新插入。  
    通知开关全部由 App 端透传，默认关闭，开启才启用。"""  
    try:  
        body = await request.json()  
        user_id = int(body["user_id"])  
        items = body.get("items", [])  
        # 全平台清理：避免 App 删光某平台绑定后服务端残留旧绑定继续推送  
        for platform in (0, 1, 2, 3):  
            await pcr_sqla.delete_bind(user_id, platform)  
        for i in items:  
            await pcr_sqla.insert_bind({  
                "user_id": user_id,  
                "pcrid": int(i["pcrid"]),  
                "platform": int(i["platform"]),  
                "name": str(i.get("name") or i["pcrid"]),  
                "group": APP_GROUP,  
                "jjc_notice":    bool(i.get("jjc_notice", False)),  
                "pjjc_notice":   bool(i.get("pjjc_notice", False)),  
                "up_notice":     int(bool(i.get("up_notice", False))),  
                "online_notice": int(i.get("online_notice", 0) or 0),  
            })  
        return web.json_response({"code": 0, "count": len(items)})  
    except Exception as e:  
        logger.error(f"app register failed: {e}")  
        return web.json_response({"code": -1, "msg": str(e)})  
  
  
async def poll(request: web.Request):  
    """App 长轮询：GET /app/poll?uid=x&timeout=30  
    返回 {"code":0,"msgs":[...]}，msgs 元素为 push_to_app 写入的 payload（dict）"""  
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
    直接入服务端账号查询队列，返回完整 /profile/get_profile 的 res 列表。  
    不走 query_all：queue 元素 (priority, counter, data) 中 DA[1] 放递增计数器，  
    避免相同 (pcrid,user_id,platform) 元组触发 function 比较的 TypeError。"""  
    try:  
        from .var import queue_dict, Platform  
        body = await request.json()  
        platform = int(body["platform"])  
        pcrids = [int(p) for p in body["pcrids"]]  
        results = []  
        done = asyncio.Event()  
        n = len(pcrids)  
  
        async def cb(st):  
            results.append(st["res"])  
            if len(results) >= n:  
                done.set()  
  
        for p in pcrids:  
            bind = PCRBind(user_id=0, pcrid=p, platform=platform,  
                           name=str(p), group=APP_GROUP)  
            # 台服按 pcrid 分两个队列，与 query1 的分流一致  
            qid = platform  
            if platform == Platform.tw_id.value:  
                qid = (Platform.tw_id.value if p // 1000000000 == 1  
                       else Platform.tw_other_id.value)  
            queue = queue_dict.get(qid)  
            if not queue:  
                logger.warn(f"平台{platform}无可用查询账号，跳过 {p}")  
                n -= 1  
                continue  
            await queue.put((1, next(_qcounter), (cb, bind, {})))  
  
        if n <= 0:  
            return web.json_response({"code": -1, "msg": "该平台无可用查询账号"})  
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