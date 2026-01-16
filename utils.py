import asyncio
from copy import deepcopy
from datetime import datetime
import traceback
from typing import List
from .img.text2img import image_draw
from hoshino import util
from hoshino.util import pic2b64
from .database.dal import JJCHistory, pcr_sqla, PCRBind
from .query import query_all
from .img.create_img import generate_info_pic, generate_support_pic, generate_talent_pic
from ..multicq_send import group_send, private_send
from nonebot import MessageSegment, logger
from hoshino.typing import CQEvent
from .var import NoticeType, Platform, platform_dict, platform_tw, query_cache, cache, lck, jjc_log
import csv
import os
from hoshino import Service, priv
from .img.rank_parse import query_knight_exp_rank
sv = Service('场号查询', enable_on_default=False, help_='输入"查群号XXX"查询对应场号的群号')

# 获取当前文件所在目录的绝对路径
current_dir = os.path.dirname(os.path.abspath(__file__))
# 构造CSV文件的相对路径（相对于当前模块目录）
# 相对于当前文件的相对路径
CSV_PATH = os.path.join(current_dir, "20250430.csv")
P_CSV_PATH = os.path.join(current_dir, "20251030.csv")
# 存储场号-群号映射
field_data = {}
p_field_data = {}

def load_csv_data():
    """从CSV文件加载数据"""
    global field_data, p_field_data
    field_data.clear()
    p_field_data.clear()
    try:
        # 加载J场数据
        with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:  # utf-8-sig处理BOM头
            reader = csv.reader(f)
            next(reader)  # 跳过标题行
            for row in reader:
                if len(row) >= 2 and row[0].isdigit() and row[1].isdigit():
                    field_data[int(row[0])] = row[1].strip()
        sv.logger.info(f"成功从CSV加载 {len(field_data)} 条J场场号数据")
        
        # 加载P场数据
        with open(P_CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            next(reader)  # 跳过标题行
            for row in reader:
                if len(row) >= 2 and row[0].isdigit() and row[1].isdigit():
                    p_field_data[int(row[0])] = row[1].strip()
        sv.logger.info(f"成功从CSV加载 {len(p_field_data)} 条P场场号数据")
    except Exception as e:
        sv.logger.error(f"加载CSV文件失败: {e}")

# 启动时加载数据
load_csv_data()

@sv.on_prefix('#查群号')
async def query_group_number(bot, ev: CQEvent):
    """查询场号对应的群号"""
    if not field_data or not p_field_data:
        await bot.send(ev, "数据加载失败，请联系管理员检查CSV文件")
        return
    
    field = ev.message.extract_plain_text().strip()
    if not field:
        await bot.send(ev, "请输入场号，例如：查群号123")
        return
    
    if not field.isdigit():
        await bot.send(ev, "场号必须是数字，例如：查群号123")
        return
    
    field_num = int(field)
    j_group_num = field_data.get(field_num)
    p_group_num = p_field_data.get(field_num)
    
    if j_group_num or p_group_num:
        msg = []
        if j_group_num:
            msg.append(f"J场{field_num}的群号是：{j_group_num}")
        if p_group_num:
            msg.append(f"P场{field_num}的群号是：{p_group_num}")
        await bot.send(ev, "\n".join(msg))
    else:
        await bot.send(ev, f"未找到场号{field_num}对应的群号")

@sv.on_fullmatch('重载场号数据')
async def reload_data(bot, ev: CQEvent):
    """手动重载数据"""
    if not priv.check_priv(ev, priv.ADMIN):
        await bot.send(ev, "只有管理员才能执行此操作")
        return
    
    await bot.send(ev, "正在重新加载场号数据...")
    load_csv_data()
    await bot.send(ev, f"场号数据已重新加载，共{len(field_data)}条J场记录，{len(p_field_data)}条P场记录")
    
class ApiException(Exception):

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def get_platform_id(ev: CQEvent) -> int:
    info: str = ev.raw_message
    return platform_dict.get(info[0], Platform.b_id.value)

def get_qid(ev: CQEvent) -> int:
    qid = ev.user_id
    for message in ev.message:
        if message.type == 'at':
            if message.data['qq'] != 'all':
                return int(message.data['qq'])
    return qid

def get_tw_platform(pcrid:int) -> str:
    return platform_tw[pcrid//1000000000]

async def query_loop(platform: int):
    start = datetime.now().timestamp()
    while True:
        try:
            # 移除info日志，不再输出开始信息
            binds = await pcr_sqla.get_bind(platform)
            if sleep_time := await query_all(binds, platform, query_rank):
                await asyncio.sleep(sleep_time)
            await asyncio.sleep(1)
            # 移除info日志，不再输出结束信息
            start = datetime.now().timestamp()
            await pcr_sqla.insert_history(jjc_log[platform])
            jjc_log[platform].clear()
        except:
            # 保留错误日志但修改输出方式，避免使用print_exc()直接输出
            logger.error(traceback.format_exc())


async def query_rank(data):
    global cache, timeStamp
    timeStamp = int(datetime.now().timestamp())
    try:
        info = data["res"]['user_info']
    except:
        return
    bind: PCRBind = data["bind_info"]
    res = [int(info['arena_rank']), int(info['grand_arena_rank']),
           int(info['last_login_time'])]
    if (bind.pcrid, bind.user_id, bind.platform) not in cache:
        cache[(bind.pcrid, bind.user_id, bind.platform)] = res
    else:
        last = deepcopy(cache[(bind.pcrid, bind.user_id, bind.platform)])
        cache[(bind.pcrid, bind.user_id, bind.platform)][0] = res[0]
        cache[(bind.pcrid, bind.user_id, bind.platform)][1] = res[1]
        cache[(bind.pcrid, bind.user_id, bind.platform)][2] = res[2]
        if res[0] != last[0]:
            await sendNotice(res[0], last[0], bind, NoticeType.jjc.value)
        if res[1] != last[1]:
            await sendNotice(res[1], last[1], bind, NoticeType.pjjc.value)
        if res[2] != last[2]:
            await sendNotice(res[2], last[2], bind, NoticeType.online.value)


async def detial_query(data):
    res = data["res"]
    bot = data["bot"]
    ev = data["ev"]
    pcrid = data["uid"]
    platfrom = data["platform"]
    try:
        logger.info('开始生成竞技场查询图片...')  # 通过log显示信息
        result_image = await generate_info_pic(res, pcrid, platfrom)
        result_image = pic2b64(result_image)  # 转base64发送，不用将图片存本地
        result_image = MessageSegment.image(result_image)
        result_support = await generate_support_pic(res, pcrid)
        result_support = pic2b64(result_support)  # 转base64发送，不用将图片存本地
        result_support = MessageSegment.image(result_support)
        talent_image = await generate_talent_pic(res)
        talent_image = pic2b64(talent_image)  # 转base64发送，不用将图片存本地
        talent_image = MessageSegment.image(talent_image)
        logger.info('竞技场查询图片已准备完毕！')
        await bot.send(ev, f"{str(result_image)}\n{result_support}\n{talent_image}", at_sender=True)
    except ApiException as e:
        await bot.send_group_msg(self_id=ev.self_id, group_id=int(ev.group_id), message=f'查询出错，{e}')


async def user_query(data: dict):
    global lck
    pcrid = data["uid"]
    info = data["info"]
    platfrom = data["platform"]
    show_group = data.get("show_group", False)  # 控制是否显示群号
    
    try:
        res = data["res"]['user_info']
        index = info[pcrid]  
        # 处理最近登录时间
        last_login = datetime.fromtimestamp(
            int(res["last_login_time"])).strftime("%m-%d %H：%M")
        # 获取JJC/PJC上升次数
        jjc_up, grand_jjc_up = await pcr_sqla.get_up_num(platfrom, pcrid, int(datetime.now().timestamp()))
        
        # 1. 公主骑士经验与RANK
        knight_exp = res.get("princess_knight_rank_total_exp", 0)
        knight_rank = await query_knight_exp_rank(knight_exp)  # 已在顶部导入，无需重复导入
        
        # 2. 构建基础额外信息（含服务器、上升次数、骑士信息）
        extra = ""
        if platfrom == Platform.tw_id.value:
            extra += f"服务器：{get_tw_platform(pcrid)}\n"
        extra += f'''上升: {jjc_up}次 / {grand_jjc_up}次\n'''
        extra += f'''公主骑士经验: {knight_exp}\n'''
        extra += f'''公主骑士RANK: {knight_rank}\n'''
        
        # 3. 火水风光暗（深域）进度
        talent_info = data["res"].get('quest_info', {}).get('talent_quest', [])
        talent_text = ""
        talent_map = {1: "🔥", 2: "💧", 3: "🍃", 4: "☀️", 5: "🌑"}  # 图案映射（可替换为自定义CQ图）
        for talent in talent_info:
            tid = talent['talent_id']
            if tid in talent_map:
                clear_count = int(talent['clear_count'])
                if clear_count:
                    char = (clear_count - 1) // 10 + 1  # 计算章节（每10关1章）
                    que_num = clear_count % 10 or 10    # 计算关卡（1-10）
                    quest = f"{char}-{que_num}"
                else:
                    quest = "未通关"
                talent_text += f"{talent_map[tid]}: {quest} "  # 格式：🔥: 2-5 
        
        # 4. 场号与群号匹配
        arena_group = res["arena_group"]
        grand_arena_group = res["grand_arena_group"]
        j_group_num = field_data.get(int(arena_group), "未知")  # J场群号
        p_group_num = p_field_data.get(int(grand_arena_group), "未知")  # P场群号
        
        # 5. 拼接最终查询文本（区分显示/不显示群号）
        if show_group:  
            query = (f'【{index+1}】{util.filt_message(str(res["user_name"]))}\n'  
                     f'{res["arena_rank"]}({arena_group}场,B服J群号:{j_group_num}) \n'  
                     f'{res["grand_arena_rank"]}({grand_arena_group}场,B服P群号:{p_group_num})\n'  
                     f'{extra}{talent_text}\n最近上号{last_login}\n\n')  
        else:  
            query = (f'【{index+1}】{util.filt_message(str(res["user_name"]))}\n'  
                     f'{res["arena_rank"]}({arena_group}场)/{res["grand_arena_rank"]}({grand_arena_group}场)\n'  
                     f'{extra}{talent_text}\n最近上号{last_login}\n\n')  
      
    except Exception as e:  
        logger.error(f"user_query 逻辑处理失败: {str(e)}")  
        logger.error(traceback.print_exc())  
        query = "查询失败（数据解析错误）\n\n"  
  
    async with lck:  
        ev = data["ev"]  
        bot = data["bot"]  
          
        # query_cache[ev.user_id]已经在__init__.py中初始化为字典  
        query_dict: dict = query_cache[ev.user_id]  
        query_dict[pcrid] = query  # 用pcrid作为key  
          
        # 当所有查询完成时,按序号排序后输出  
        if len(query_dict) == len(info):  
            # 按info字典中的序号排序  
            sorted_items = sorted(info.items(), key=lambda x: x[1])  
            sorted_queries = [query_dict[pcrid_key] for pcrid_key, _ in sorted_items]  
            msg = ''.join(sorted_queries)  
              
            if len(msg) > 800:  
                msg = f'[CQ:image,file={image_draw(msg)}]'  
              
            try:  
                if hasattr(ev, 'group_id') and ev.group_id:  
                    await bot.send_group_msg(  
                        self_id=ev.self_id,  
                        group_id=int(ev.group_id),  
                        message=msg  
                    )  
                elif hasattr(ev, 'user_id') and ev.user_id:  
                    await bot.send_private_msg(  
                        self_id=ev.self_id,  
                        user_id=int(ev.user_id),  
                        message=msg  
                    )  
            except Exception as send_e:  
                logger.error(f"user_query 消息发送失败: {str(send_e)}")  
                logger.error(traceback.print_exc())  
              
            del query_cache[ev.user_id]


async def bind_pcrid(data):
    bot = data["bot"]
    ev = data["ev"]
    pcrid = data["uid"]
    info: dict = data["info"]
    try:
        res = data["res"]['user_info']
        qid = ev.user_id
        have_bind: List[PCRBind] = await pcr_sqla.get_bind(info["platform"], qid)
        bind_num = len(have_bind)
        if bind_num >= 999:
            reply = '您订阅了太多账号啦！'
        elif pcrid in [bind.pcrid for bind in have_bind]:
            reply = '这个uid您已经订阅过了，不要重复订阅！'
        else:
            info["name"] = info["name"] if info["name"] else util.filt_message(str((res["user_name"])))
            await pcr_sqla.insert_bind(info)
            reply = '添加成功！已为您开启群聊推送！'
    except:
        logger.error(traceback.format_exc())
        reply = f'找不到这个uid，大概率是你输错了！'
    await bot.send_group_msg(self_id=ev.self_id, group_id=int(ev.group_id), message=reply)


async def sendNotice(new: int, old: int, info: PCRBind, noticeType: int):  
    global timeStamp, jjc_log  
      
    # 检查 Service 是否在目标群启用  
    from . import sv_b, sv_qu, sv_tw  
      
    if not info.private:  
        if info.platform == 0:  
            service = sv_b  
        elif info.platform == 1:  
            service = sv_qu  
        else:  
            service = sv_tw  
          
        if not service.check_enable(info.group):  
            logger.info(f'Service disabled in group {info.group}, skip notice')  
            return  
      
    if noticeType == NoticeType.online.value:  
        change = '上线了！' 
    else:
        if noticeType == NoticeType.jjc.value:
            change = '\njjc: '
        else:
            change = '\npjjc: '
        if new < old:
            change += f'''{old}->{new} [▲{old-new}]'''
        else:
            change += f'''{old}->{new} [▽{new-old}]'''
# -----------------------------------------------------------------
    msg = ''
    onlineNotice = False
    is_send = False
    if info.online_notice and noticeType == NoticeType.online.value:
        if (new-old) < (60 if info.online_notice == 3 else 60 * 10):
            cache[(info.pcrid, info.user_id, info.platform)][2] = old  # 间隔太短，不更新缓存
        # 类型1，只在特定时间播报
        elif info.online_notice != 1 or ((new % 86400//3600+8) % 24 == 14 and new % 3600 // 60 >= 30):
            onlineNotice = True

    if (((noticeType == NoticeType.jjc.value and info.jjc_notice) or
         (noticeType == NoticeType.pjjc.value and info.pjjc_notice)) and
            (info.up_notice or (new > old))) or (noticeType == NoticeType.online.value and onlineNotice):
        logger.info(f'Send Notice FOR {info.user_id}({info.pcrid})')
        msg = info.name + change
        is_send = True
        if info.private:
            await private_send(int(info.user_id), msg)
        else:
            await group_send(info.group, msg + f'[CQ:at,qq={info.user_id}]')
    if (noticeType != NoticeType.online.value) or is_send: #上线提醒没报的没必要记录
        jjc_log[info.platform].append(JJCHistory(user_id=info.user_id,
                                                pcrid=info.pcrid,
                                                name=info.name,
                                                platform=info.platform,
                                                date=timeStamp,
                                                before=old,
                                                after=new,
                                                is_send=is_send,
                                                item=noticeType
                                                ))