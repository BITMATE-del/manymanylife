import asyncio, contextlib, json, os, re, secrets, sqlite3, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv("DATA_DIR",str(ROOT/"data")))
DATA.mkdir(parents=True,exist_ok=True)
IMAGES=DATA/"images"; IMAGES.mkdir(exist_ok=True)
DB=DATA/"bot.sqlite3"
TZ=ZoneInfo(os.getenv("TIMEZONE","Asia/Seoul"))
TOKEN=os.getenv("BOT_TOKEN","")
GROUP=os.getenv("GROUP_CHAT_ID","")
USER=os.getenv("ADMIN_USER","admin")
PASSWORD=os.getenv("ADMIN_PASSWORD","")
app=FastAPI(title="그룹 자동 운영")
security=HTTPBasic()

def guard(creds:HTTPBasicCredentials=Depends(security)):
    if not PASSWORD or not secrets.compare_digest(creds.username,USER) or not secrets.compare_digest(creds.password,PASSWORD):
        raise HTTPException(401,"관리자 인증 실패",headers={"WWW-Authenticate":"Basic"})
def db():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    return c
def now(): return datetime.now(TZ)
def group_id():
    with db() as c:
        r=c.execute("SELECT chat_id FROM groups WHERE active=1 ORDER BY rowid LIMIT 1").fetchone()
        return r["chat_id"] if r else GROUP
def track_group(chat):
    ident=str(chat.get("id",""))
    if not ident or chat.get("type") not in ("group","supergroup"):return
    title=str(chat.get("title") or ident)[:200]
    with db() as c:
        c.execute("INSERT INTO groups(chat_id,title,active) VALUES(?,?,1) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,active=1",(ident,title))
def initialize():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS schedule(id TEXT PRIMARY KEY,title TEXT NOT NULL,time TEXT NOT NULL,end_time TEXT,interval INTEGER,days TEXT NOT NULL,body TEXT NOT NULL,images TEXT NOT NULL,action TEXT NOT NULL,active INTEGER NOT NULL,priority INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS groups(chat_id TEXT PRIMARY KEY,title TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT NOT NULL,minute TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(id,minute));
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,detail TEXT NOT NULL);
        """)
        if not c.execute("SELECT 1 FROM settings WHERE key='init'").fetchone():
            items=[("08:55","보이스룸 1차","send",100),("09:00","채팅 열기","open",10),("14:00","휴식 안내","send",100),("15:00","휴식 안내","send",100),("16:00","휴식 안내","send",100),("17:55","보이스룸 2차","send",100),("20:00","마감 안내","send_close",200)]
            for t,title,action,priority in items:
                c.execute("INSERT INTO schedule VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uuid.uuid4().hex,title,t,None,None,json.dumps(list(range(7))),"", "[]",action,1,priority))
            c.execute("INSERT INTO schedule VALUES(?,?,?,?,?,?,?,?,?,?,?)",(uuid.uuid4().hex,"매시간 정기 공지","09:00","20:00",60,json.dumps(list(range(7))),"","[]","send",1,100))
            c.execute("INSERT INTO settings VALUES('welcome',?)",(json.dumps({"active":True,"body":"텔닉({nickname})님 반갑습니다.","images":[]},ensure_ascii=False),))
            c.execute("INSERT INTO settings VALUES('init','1')")
def log(s):
    with db() as c:c.execute("INSERT INTO logs(at,detail) VALUES(?,?)",(now().isoformat(timespec="seconds"),str(s)[:400]))
def readable(row):
    x=dict(row);x["days"]=json.loads(x["days"]);x["images"]=json.loads(x["images"]);x["active"]=bool(x["active"]);return x

class Schedule(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    time:str
    end_time:str|None=None
    interval:int|None=None
    days:list[int]=Field(default_factory=lambda:list(range(7)))
    body:str=Field(default="",max_length=4096)
    images:list[str]=Field(default_factory=list,max_length=10)
    action:str="send"
    active:bool=True
    priority:int=Field(default=100,ge=0,le=999)

def validate(s):
    valid=lambda v:isinstance(v,str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",v)
    if not valid(s.time) or (s.interval is not None and (not valid(s.end_time) or s.end_time<s.time or not 1<=s.interval<=1440)):raise HTTPException(400,"시간/반복 간격이 올바르지 않습니다")
    if s.action not in ("send","open","close","send_close") or not s.days or any(type(x)!=int or x<0 or x>6 for x in s.days):raise HTTPException(400,"실행 유형 또는 요일 오류")
    if len(set(s.images))!=len(s.images):raise HTTPException(400,"이미지 중복")
    with db() as c:
        if any(not c.execute("SELECT 1 FROM media WHERE id=?",(i,)).fetchone() for i in s.images):raise HTTPException(400,"등록되지 않은 이미지")
def save(s,ident):
    validate(s)
    with db() as c:c.execute("INSERT OR REPLACE INTO schedule VALUES(?,?,?,?,?,?,?,?,?,?,?)",(ident,s.title,s.time,s.end_time,s.interval,json.dumps(s.days),s.body,json.dumps(s.images),s.action,int(s.active),s.priority))
    return {"ok":True,"id":ident}
@app.get("/",response_class=HTMLResponse,dependencies=[Depends(guard)])
def home():return (ROOT/"index.html").read_text(encoding="utf-8")
@app.get("/health")
def health():return {"ok":True,"configured":bool(TOKEN and group_id())}
@app.get("/api/state",dependencies=[Depends(guard)])
def state():
    with db() as c:
        return {"items":[readable(x) for x in c.execute("SELECT * FROM schedule ORDER BY time,priority")],
        "media":[dict(x) for x in c.execute("SELECT id,name FROM media")],
        "welcome":json.loads(c.execute("SELECT value FROM settings WHERE key='welcome'").fetchone()["value"]),
        "logs":[dict(x) for x in c.execute("SELECT at,detail FROM logs ORDER BY id DESC LIMIT 30")],
        "configured":bool(TOKEN and group_id()),"groups":[dict(x) for x in c.execute("SELECT chat_id,title,active FROM groups ORDER BY title")],"selected_group":group_id()}
@app.put("/api/group/{ident}",dependencies=[Depends(guard)])
def choose_group(ident:str):
    with db() as c:
        if not c.execute("SELECT 1 FROM groups WHERE chat_id=? AND active=1",(ident,)).fetchone():raise HTTPException(404,"등록된 그룹이 아닙니다")
        c.execute("UPDATE groups SET active=CASE WHEN chat_id=? THEN 1 ELSE 0 END",(ident,))
    return {"ok":True}
@app.post("/api/schedule",dependencies=[Depends(guard)])
def create(s:Schedule):return save(s,uuid.uuid4().hex)
@app.put("/api/schedule/{ident}",dependencies=[Depends(guard)])
def update(ident:str,s:Schedule):
    with db() as c:
        if not c.execute("SELECT 1 FROM schedule WHERE id=?",(ident,)).fetchone():raise HTTPException(404,"일정 없음")
    return save(s,ident)
@app.delete("/api/schedule/{ident}",dependencies=[Depends(guard)])
def delete(ident:str):
    with db() as c:c.execute("DELETE FROM schedule WHERE id=?",(ident,))
    return {"ok":True}
@app.post("/api/media",dependencies=[Depends(guard)])
async def upload(file:UploadFile=File(...)):
    data=await file.read(10_000_001)
    if len(data)>10_000_000:raise HTTPException(400,"10MB 이하 이미지만 가능합니다")
    head=data[:16]
    ext="jpg" if head.startswith(b"\xff\xd8\xff") else "png" if head.startswith(b"\x89PNG\r\n\x1a\n") else "webp" if head.startswith(b"RIFF") and head[8:12]==b"WEBP" else None
    if not ext:raise HTTPException(400,"이미지 파일이 아닙니다")
    ident=uuid.uuid4().hex; path=IMAGES/(ident+"."+ext);path.write_bytes(data)
    with db() as c:c.execute("INSERT INTO media VALUES(?,?,?)",(ident,Path(file.filename or "image").name[:100],str(path)))
    return {"ok":True,"id":ident}
@app.get("/api/media/{ident}",dependencies=[Depends(guard)])
def show_media(ident:str):
    with db() as c:r=c.execute("SELECT path FROM media WHERE id=?",(ident,)).fetchone()
    if not r:raise HTTPException(404,"이미지 없음")
    return FileResponse(r["path"])
@app.delete("/api/media/{ident}",dependencies=[Depends(guard)])
def delete_media(ident:str):
    with db() as c:
        r=c.execute("SELECT path FROM media WHERE id=?",(ident,)).fetchone()
        if not r:raise HTTPException(404,"이미지 없음")
        for row in c.execute("SELECT images FROM schedule"):
            if ident in json.loads(row["images"]):raise HTTPException(409,"일정에서 사용 중인 이미지입니다")
        welcome=json.loads(c.execute("SELECT value FROM settings WHERE key='welcome'").fetchone()["value"])
        if ident in welcome["images"]:raise HTTPException(409,"환영 메시지에서 사용 중인 이미지입니다")
        c.execute("DELETE FROM media WHERE id=?",(ident,))
    Path(r["path"]).unlink(missing_ok=True)
    return {"ok":True}
class Welcome(BaseModel):
    active:bool=True
    body:str=Field(default="텔닉({nickname})님 반갑습니다.",max_length=4096)
    images:list[str]=Field(default_factory=list,max_length=10)
@app.put("/api/welcome",dependencies=[Depends(guard)])
def welcome(w:Welcome):
    with db() as c:
        for ident in w.images:
            if not c.execute("SELECT 1 FROM media WHERE id=?",(ident,)).fetchone():raise HTTPException(400,"없는 이미지")
        c.execute("UPDATE settings SET value=? WHERE key='welcome'",(w.model_dump_json(),))
    return {"ok":True}

async def telegram(method,payload=None,files=None):
    if not TOKEN:raise RuntimeError("BOT_TOKEN 미설정")
    async with httpx.AsyncClient(timeout=35) as client:
        if files:r=await client.post("https://api.telegram.org/bot"+TOKEN+"/"+method,data=payload,files=files)
        else:r=await client.post("https://api.telegram.org/bot"+TOKEN+"/"+method,json=payload or {})
        j=r.json()
        if not j.get("ok"):raise RuntimeError(str(j.get("description",r.status_code)))
        return j["result"]
async def send(body,images):
    if not images:
        if body.strip():await telegram("sendMessage",{"chat_id":group_id(),"text":body})
        return
    if len(images)==1:
        with db() as c:r=c.execute("SELECT path FROM media WHERE id=?",(images[0],)).fetchone()
        if not r:raise RuntimeError("첨부 이미지 누락")
        path=Path(r["path"])
        with path.open("rb") as f:await telegram("sendPhoto",{"chat_id":group_id(),"caption":body[:1024]},{"photo":(path.name,f,"image/jpeg" if path.suffix==".jpg" else "image/png" if path.suffix==".png" else "image/webp")})
    else:
        streams=[]
        try:
            with db() as c:
                paths=[]
                for mid in images:
                    r=c.execute("SELECT path FROM media WHERE id=?",(mid,)).fetchone()
                    if not r:raise RuntimeError("첨부 이미지 누락")
                    paths.append(Path(r["path"]))
            files={}
            album=[]
            for i,p in enumerate(paths):
                f=p.open("rb");streams.append(f)
                files["p"+str(i)]=(p.name,f,"image/jpeg" if p.suffix==".jpg" else "image/png" if p.suffix==".png" else "image/webp")
                album.append({"type":"photo","media":"attach://p"+str(i),**({"caption":body[:1024]} if i==0 else {})})
            await telegram("sendMediaGroup",{"chat_id":GROUP,"media":json.dumps(album,ensure_ascii=False)},files)
        finally:
            for f in streams:f.close()
    if len(body)>1024:await telegram("sendMessage",{"chat_id":GROUP,"text":body})
async def chat(opened):
    permissions={k:opened for k in ("can_send_messages","can_send_audios","can_send_documents","can_send_photos","can_send_videos","can_send_video_notes","can_send_voice_notes","can_send_polls","can_send_other_messages","can_add_web_page_previews")}
    permissions["can_invite_users"]=True
    await telegram("setChatPermissions",{"chat_id":GROUP,"permissions":permissions,"use_independent_chat_permissions":True})
async def execute(s):
    if s["action"]=="open":await chat(True)
    await send(s["body"],s["images"])
    if s["action"] in ("close","send_close"):await chat(False)
@app.post("/api/test/{ident}",dependencies=[Depends(guard)])
async def test(ident:str):
    with db() as c:r=c.execute("SELECT * FROM schedule WHERE id=?",(ident,)).fetchone()
    if not r:raise HTTPException(404,"일정 없음")
    try:await execute(readable(r))
    except Exception as e:raise HTTPException(502,str(e))
    log("수동 실행: "+r["title"]);return {"ok":True}
def due(s,t):
    if t.weekday() not in s["days"]:return False
    hh,mm=map(int,s["time"].split(":"));first=hh*60+mm;cur=t.hour*60+t.minute
    if s["interval"] is None:return cur==first
    a,b=map(int,s["end_time"].split(":"))
    return first<=cur<=a*60+b and (cur-first)%s["interval"]==0
async def clock():
    while True:
        try:
            if TOKEN and group_id():
                t=now(); key=t.strftime("%Y-%m-%dT%H:%M")
                with db() as c:items=[readable(r) for r in c.execute("SELECT * FROM schedule WHERE active=1 ORDER BY priority,time")]
                for s in items:
                    if not due(s,t):continue
                    with db() as c:insert=c.execute("INSERT OR IGNORE INTO runs VALUES(?,?,'started')",(s["id"],key))
                    if not insert.rowcount:continue
                    try:
                        await execute(s)
                        with db() as c:c.execute("UPDATE runs SET result='ok' WHERE id=? AND minute=?",(s["id"],key))
                        log("완료: "+s["title"])
                    except Exception as e:
                        with db() as c:c.execute("UPDATE runs SET result=? WHERE id=? AND minute=?",("failed: "+str(e)[:150],s["id"],key))
                        log("실패: "+s["title"]+" "+str(e))
        except Exception as e:log("스케줄 오류 "+str(e))
        await asyncio.sleep(5)
async def poll():
    offset=None
    while True:
        try:
            if not TOKEN:
                await asyncio.sleep(10);continue
            p={"timeout":25,"allowed_updates":["message","my_chat_member"]}
            if offset is not None:p["offset"]=offset
            for u in await telegram("getUpdates",p):
                offset=u["update_id"]+1
                evt=u.get("my_chat_member",{})
                if evt:
                    chat=evt.get("chat",{})
                    status=evt.get("new_chat_member",{}).get("status","")
                    if status in ("member","administrator"):track_group(chat)
                    elif status in ("left","kicked"):
                        with db() as c:c.execute("UPDATE groups SET active=0 WHERE chat_id=?",(str(chat.get("id")),))
                msg=u.get("message",{})
                if msg.get("chat",{}).get("type") in ("group","supergroup"):track_group(msg["chat"])
                if str(msg.get("chat",{}).get("id"))!=group_id():continue
                members=msg.get("new_chat_members",[])
                if not members:continue
                with db() as c:cfg=json.loads(c.execute("SELECT value FROM settings WHERE key='welcome'").fetchone()["value"])
                if not cfg["active"]:continue
                for m in members:
                    if m.get("is_bot"):continue
                    nickname=(m.get("first_name","")+" "+m.get("last_name","")).strip() or str(m["id"])
                    try:await send(cfg["body"].replace("{nickname}",nickname),cfg["images"])
                    except Exception as e:log("환영 전송 실패 "+str(e))
        except Exception as e:
            log("폴링 오류 "+str(e));await asyncio.sleep(5)
@app.on_event("startup")
async def start():
    initialize()
    if GROUP:
        with db() as c:c.execute("INSERT OR IGNORE INTO groups(chat_id,title,active) VALUES(?,?,1)",(GROUP,"기본 그룹",1))
    app.state.tasks=[asyncio.create_task(clock()),asyncio.create_task(poll())]
@app.on_event("shutdown")
async def stop():
    for t in app.state.tasks:t.cancel()
    for t in app.state.tasks:
        with contextlib.suppress(asyncio.CancelledError):await t
