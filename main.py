# -*- coding: utf-8 -*-
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
from pyrogram import Client, filters
from pyrogram.types import ChatMemberUpdated, BotCommand
import sqlite3
import asyncio
from datetime import datetime
from flask import Flask
import threading

# ========== 从云端环境变量读取（Render 里配置）==========
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
# =======================================================

app = Client(
    "risk_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# 保活网页：Render 需要服务监听端口才会保持健康
web = Flask(__name__)


@web.route("/")
def alive():
    return "risk bot alive"


def run_web():
    web.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


def init_db():
    conn = sqlite3.connect("risk.db")
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS member_record (
        tg_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        join_time TEXT,
        risk_score INTEGER,
        is_black INTEGER DEFAULT 0
    )''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS blacklist (
        tg_id INTEGER PRIMARY KEY,
        remark TEXT
    )''')
    conn.commit()
    conn.close()


init_db()


def get_risk_score(user):
    score = 0
    if not user.username:
        score += 15
    if not user.bio:
        score += 15
    if not user.photo:
        score += 20
    return score


@app.on_chat_member_updated()
async def scan_new_user(client: Client, update: ChatMemberUpdated):
    # 只监听【新加入成员】
    if not (update.new_chat_member and update.new_chat_member.status == "member"):
        return

    user = update.new_chat_member.user
    uid = user.id
    username = user.username
    firstname = user.first_name
    join_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("risk.db")
    cur = conn.cursor()

    # 黑名单检测
    cur.execute("SELECT * FROM blacklist WHERE tg_id=?", (uid,))
    black_data = cur.fetchone()
    if black_data:
        msg_text = (
            f"⚠️【黑名单账号入群提醒】\n"
            f"@用户名: @{username if username else '无'}\n"
            f"昵称: {firstname}\n"
            f"ID: {uid}"
        )
        await client.send_message(OWNER_ID, msg_text)
        cur.execute('''INSERT OR REPLACE INTO member_record
            (tg_id, username, first_name, join_time, risk_score, is_black)
            VALUES (?,?,?,?,?,?)''', (uid, username, firstname, join_time_str, 100, 1))
        conn.commit()
        conn.close()
        return

    # 风险打分
    score = get_risk_score(user)
    cur.execute('''INSERT OR REPLACE INTO member_record
        (tg_id, username, first_name, join_time, risk_score, is_black)
        VALUES (?,?,?,?,?,?)''', (uid, username, firstname, join_time_str, score, 0))
    conn.commit()
    conn.close()

    # 高风险才私聊告警（≥60分），用户无感
    if score >= 60:
        alert_msg = (
            f"⚠️【疑似小号 静默告警】\n"
            f"@用户名：@{username if username else '无'}\n"
            f"昵称：{firstname}\n"
            f"TG ID：{uid}\n"
            f"风险分：{score}\n"
            f"👉 判定特征：无头像/无简介/无用户名"
        )
        await client.send_message(OWNER_ID, alert_msg)


async def get_user_id_from_input(client, input_str):
    # 支持数字ID 或 @用户名
    if input_str.lstrip("-").isdigit():
        return int(input_str)
    if input_str.startswith("@"):
        username = input_str.lstrip("@")
        try:
            u = await client.get_users(username)
            return u.id
        except Exception:
            return None
    return None


@app.on_message(filters.command("addblack"))
async def add_blacklist(client, msg):
    if msg.from_user.id != OWNER_ID:
        return
    args = msg.command
    if len(args) < 2:
        await msg.reply_text("用法：/addblack @用户名 备注\n也可以直接填数字ID")
        return
    tar = args[1]
    remark = " ".join(args[2:]) if len(args) >= 3 else "手动拉黑"
    tar_id = await get_user_id_from_input(client, tar)
    if tar_id is None:
        await msg.reply_text("❌ 找不到这个用户！检查用户名或者ID")
        return
    conn = sqlite3.connect("risk.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO blacklist(tg_id, remark) VALUES (?,?)", (tar_id, remark))
    conn.commit()
    conn.close()
    await msg.reply_text(f"✅ 已加入黑名单：{tar}")


@app.on_message(filters.command("delblack"))
async def del_blacklist(client, msg):
    if msg.from_user.id != OWNER_ID:
        return
    args = msg.command
    if len(args) < 2:
        await msg.reply_text("用法：/delblack @用户名\n也可以直接填数字ID")
        return
    tar = args[1]
    tar_id = await get_user_id_from_input(client, tar)
    if tar_id is None:
        await msg.reply_text("❌ 找不到这个用户！检查用户名或者ID")
        return
    conn = sqlite3.connect("risk.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM blacklist WHERE tg_id=?", (tar_id,))
    conn.commit()
    conn.close()
    await msg.reply_text(f"✅ 已移出黑名单：{tar}")


@app.on_message(filters.command("checku"))
async def check_user(client, msg):
    if msg.from_user.id != OWNER_ID:
        return
    args = msg.command
    if len(args) < 2:
        await msg.reply_text("用法：/checku @用户名\n也可以直接填数字ID")
        return
    tar = args[1]
    tar_id = await get_user_id_from_input(client, tar)
    if tar_id is None:
        await msg.reply_text("❌ 找不到这个用户！检查用户名或者ID")
        return
    conn = sqlite3.connect("risk.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM member_record WHERE tg_id=?", (tar_id,))
    res = cur.fetchone()
    conn.close()
    if res:
        text = (
            f"📋 用户记录\n"
            f"@用户名: @{res[1] if res[1] else '无'}\n"
            f"昵称: {res[2]}\n"
            f"入群时间: {res[3]}\n"
            f"风险分: {res[4]}\n"
            f"黑名单: {'是' if res[5] == 1 else '否'}"
        )
        await msg.reply_text(text)
    else:
        await msg.reply_text("❌ 没有找到该用户记录")


async def main():
    async with app:
        # 注册左下角菜单
        await app.set_bot_commands([
            BotCommand("addblack", "添加黑名单(@用户名/ID)"),
            BotCommand("delblack", "移除黑名单(@用户名/ID)"),
            BotCommand("checku", "查询用户风险(@用户名/ID)")
        ])
        me = await app.get_me()
        print("=" * 40)
        print(f"✅ Bot启动成功！名称: {me.first_name} | ID: {me.id}")
        print("✅ 左下角菜单已加载，等待新人入群...")
        print("=" * 40)
        # 保持运行
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    # 启动保活网页（独立线程）
    threading.Thread(target=run_web, daemon=True).start()
    asyncio.run(main())
