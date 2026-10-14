import asyncio  
import itertools  
from aiohttp import web  
from nonebot import logger  
from .database.dal import PCRBind  
from .var import Platform, queue_dict  
  
APP_PORT = 8089  
_qcounter = itertools.count()   # 队列元组第二位，防相同 (pcrid,user_id,platform) 触发 function 比较  
  
  
async def app_query(request: web.Request):  
    """App 即时查询：POST {platform: int, pcrids: [int]}  
    直接入服务端账号查询队列，返回完整 /profile/get_profile 的 res 列表。"""  
    try:  
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
            bind = PCRBind(user_id=0, pcrid=p, platform=platform, name=str(p), group=0)  
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
    app.router.add_post("/app/query", app_query)  
    runner = web.AppRunner(app)  
    await runner.setup()  
    await web.TCPSite(runner, "0.0.0.0", APP_PORT).start()  
    logger.info(f"pcrjjc2 app query server listening on :{APP_PORT}")