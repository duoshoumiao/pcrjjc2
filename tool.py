from asyncio import get_event_loop
from json import load
from os.path import join, dirname, isfile
import glob
from .database.dal import pcr_sqla
from .client.playerpref import decryptxml
account_path = join(dirname(__file__), "account")

# 这是个迁移工具，不是插件：account.json 不存在时不要让 import 直接崩掉，
# 否则 nonebot.load_plugins 会把它当插件加载并报 Failed to import。
acinfo: list = []
_account_json = join(account_path, 'account.json')
if isfile(_account_json):
    with open(_account_json, encoding="utf-8") as fp:
        acinfo = load(fp)
else:
    print(f"[pcrjjc_huannai2.tool] 未找到 {_account_json}，迁移功能不可用（不影响正常使用）")

xml_files = glob.glob(join(account_path, "*.xml"))
for xml_file in xml_files:
    tw_account = decryptxml(xml_file)

    acinfo.append({
            "viewer_id": tw_account['VIEWER_ID_lowBits'],
            "account": tw_account['UDID'],
            "password": tw_account['SHORT_UDID_lowBits'],
            "platform": 2
        })

async def refresh_account():
    await pcr_sqla._create_all()
    await pcr_sqla.delete_all_account()
    await pcr_sqla.insert_account(acinfo)
    print("done")

async def recover_binds():
    with open(join(account_path, 'bind1.json')) as fp:
        binds: list = load(fp)
        binds = binds["arena_bind"]
        for user in binds:
            player = binds[user]
            for i, pcrid in enumerate(player["pcrid"]):
                await pcr_sqla.insert_bind({"platform": 0,
                                                "pcrid": pcrid,
                                                "name": player["pcrName"][i],
                                                "group": player["gid"],
                                                "user_id": int(user),
                                                "jjc_notice": bool(player["noticeType"][i]//1000),
                                                "pjjc_notice": bool(player["noticeType"][i] % 1000//100),
                                                "up_notice": bool(player["noticeType"][i] % 100//10),
                                                "online_notice": player["noticeType"][i] % 10
                                                }
                                               )
    with open(join(account_path, 'bind2.json')) as fp:
        binds: list = load(fp)
        binds = binds["arena_bind"]
        for user in binds:
            player = binds[user]
            for i, pcrid in enumerate(player["pcrid"]):
                await pcr_sqla.insert_bind({"platform": 1,
                                                "pcrid": pcrid,
                                                "name": player["pcrName"][i],
                                                "group": player["gid"],
                                                "user_id": int(user),
                                                "jjc_notice": bool(player["noticeType"][i]//1000),
                                                "pjjc_notice": bool(player["noticeType"][i] % 1000//100),
                                                "up_notice": bool(player["noticeType"][i] % 100//10),
                                                "online_notice": player["noticeType"][i] % 10
                                                }
                                               )
async def ADD_COLUMN():
    async with pcr_sqla.async_session() as session:
        async with session.begin():
            await session.execute('ALTER TABLE JJCHistory ADD COLUMN is_send BOOLEAN DEFAULT 1')
            return 1
