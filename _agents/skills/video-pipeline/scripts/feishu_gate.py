#!/usr/bin/env python3
"""feishu_gate.py — driver 飞书收发模块（wayfinder #30 决议2：确认门进程内轮询）。

职责：
  - 发送：进度通知（text）/ 成片（file）/ 封面（image）到读报群
  - 接收：轮询群历史消息，等用户回复；分类 通过/重生成/反馈
  - 与 IM 助理共存（2026-10-16 用户拍板：切 DSH 飞书，弃 Hermes）：REST 历史消息
    轮询是只读拉取、不消费事件；确认门群里不 @ 机器人 ⇒ 助理侧（websocket 事件推送）
    根本收不到那条消息，两边不抢同一条回复

凭证（DSH 侧，不落仓库；T13 迁移 2026-10-16）：
  ~/.dsh/dsh-im-connect/channels.json 的 channels[env FEISHU_CHANNEL].config
    appId / appSecret（与 dsh-im-connect 插件共用同一套应用凭据）
  目标群：env FEISHU_GATE_CHAT_ID 或常量 GATE_CHAT_ID
  （读报确认门群，机器人 dsh-win 已入群）
  兜底：上面取不到时回退 ~/.hermes/.env 的 FEISHU_*（旧口径，保留兼容）

用法（模块）：
    from feishu_gate import FeishuGate
    gate = FeishuGate()
    gate.send_text("阶段4 完成")
    r = gate.gate_confirm(file_path="成片.mp4", caption="成片审核④，回复【通过】继续")
    # r["action"] in ("pass", "retry", "abort")；r["status"] 原始 6 值
    # （pass/shot_regen/regen/feedback/timeout/error，台账留痕与文案用）

用法（CLI 自检）：
    feishu_gate.py send "测试消息"
    feishu_gate.py image <path> [--caption 文案]
    feishu_gate.py poll [--since-min 10]     # 拉最近群消息（验证历史消息权限）
    feishu_gate.py gate --timeout 120        # 真发联调消息并等回复（验确认门闭环）
"""
import glob
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
import urllib.error

HERMES_ENV = os.path.expanduser("~/.hermes/.env")  # 兼容别名（旧口径兜底凭证源）
DEFAULT_CHANNEL = "feishu_439061570ff1"  # DSH 侧飞书渠道（应用 dsh-win）


def _channels_path():
    """channels.json 定位（双端，跨端铁律 PR #124/69756f3）：env 覆盖 > 本机 ~/.dsh >
    WSL 看 Windows 盘。★WSL 侧 HOME=/home/lmr，而 DSH 的 channels.json 在
    C:\\Users\\<winuser>\\.dsh\\（2026-10-16 实测：只写 ~/.dsh 会让 WSL 里 driver
    静默回落 hermes .env → 读到旧应用与旧会话）。"""
    env = (os.environ.get("FEISHU_CHANNELS_JSON") or "").strip()
    if env:
        return env
    cands = [os.path.expanduser("~/.dsh/dsh-im-connect/channels.json")]
    cands += sorted(glob.glob("/mnt/c/Users/*/.dsh/dsh-im-connect/channels.json"))
    for p in cands:
        if os.path.exists(p):
            return p
    return cands[0]


CHANNELS_JSON = _channels_path()
# 读报确认门群（T13 新建，机器人已入群）；群里不 @ 机器人 ⇒ 助理侧静默
GATE_CHAT_ID = "oc_ea0c73e60dd92cc1362ef95d0049de29"
TOKEN_CACHE = os.path.join(tempfile.gettempdir(), "_feishu_tenant_token.json")
# 服务端「token 无效」业务码（99991663 = Invalid access token for authorization）。
# 2026-10-17 事故：token 在 expire 之前被服务端判无效，旧实现既不重取也不重试，
# 20 次连续失败后把整扇确认门判 error → driver code=4（s13 从未执行）。
AUTH_ERR_CODES = {99991663}
# 轮询失败放弃阈值：连续 20 次（poll_s 默认 10s ⇒ 实耗约 3.3 分钟；旧注释「约 10 分钟」
# 与实现不符）。阈值沿用（test_feishu_gate 2c 钉死），只额外做到每次失败都落日志，
# 让 token 失效这类静默期能从日志看出起点、错误码与连续时长。
POLL_FAIL_MAX = 20

PASS_WORDS = {"通过", "ok", "OK", "Ok", "可以", "确认", "行", "好", "yes", "Yes", "YES", "y", "Y", "👍", "👍🏻", "通过✅", "✅"}
REGEN_WORDS = ["重生成", "重来", "重新生成", "重做", "重跑", "换一", "不合格"]
# T17 #129 人工指认词：文本命中即「指认翻转」语义（存证+真值回填+重烧复判）。
# 保守集：不含单字「躺」（「躺平」误伤面大）与「歪」（test 既有断言「标题有点歪」
# 须留 feedback）；指认词判定优先级见 classify 判定树注释。
FLAG_WORDS = ("翻转", "横躺", "横着", "竖着", "旋转", "倒了", "倒着")
# 门内政策表（票8 #67 Q1）：status → driver 执行指令。feedback/timeout/error 都走
# abort（driver notify 叫人 + _flush_exit）；shot_regen/regen 都走 retry（regen_fn
# 重做后重新送审）；pass 即放行；flag 走 retry（T17 #129 指认回路：存证+真值回填
# +指名段重烧复判，不停机）。分裂脑合拢：反应政策住进这行表，不再散在 driver。
_ACTION = {"pass": "pass", "shot_regen": "retry", "regen": "retry", "flag": "retry",
           "feedback": "abort", "timeout": "abort", "error": "abort"}
# shot 前缀镜头指称（#69 候选1）：classify 探测与 parse 拾取共用此式，防两处形态漂移。
# 左界 (?<![a-zA-Z]) 排除 screenshot 类内嵌词；右界 (?!\d) 拒三位截断（shot100 不误成 shot10）
_SHOT_RE = re.compile(r"(?<![a-zA-Z])shot\s*0*(\d{1,2})(?!\d)", re.I)
# 斜杠/顿号列举链（2026-10-20 事故）：用户写「shot01/05/08/12/14/19 是问题镜头」时，
# 旧实现只认首段 shot 前缀，后五段裸数字在没有 REGEN 词的情况下被静默丢弃 —— classify
# 因首段命中仍判 shot_regen，于是「静默少做」而不是叫人，是本模块最危险的一类退化。
# 此式只吃「紧跟在一个 shotNN 之后、由列举分隔符连接的裸数字」，不放开任意裸数字
#（「聊天数字 7 号到货」这类仍不拾，见 test 第 5 节守卫断言）。
_SHOT_CHAIN_RE = re.compile(
    r"(?<![a-zA-Z])shot\s*0*(\d{1,2})(?!\d)"
    r"((?:\s*[/、,，]\s*\d{1,2}(?![\d.])(?!\s*[-~]\s*\d))+)", re.I)


def _glog(msg, dry=False):
    """#49 门进展日志：带时刻一行，driver 日志里门等待期可见（配合 #48 硬退出）。
    只写进展（发送成功/轮询起止/回复判定）；失败仍走 send_* 原有打印，行为不变。
    dry 模式下带 [dry] 标记（同 [feishu_gate][dry] 惯例），声明未真发。
    FEISHU_GATE_SILENT=1 时静音（凭证源留痕等噪声日志，测试用）。"""
    if os.environ.get("FEISHU_GATE_SILENT") == "1":
        return
    print(f"[gate {time.strftime('%H:%M:%S')}] {'[dry] ' if dry else ''}{msg}",
          flush=True)


def _load_env():
    """读飞书凭证（不回显值）。T13 起 DSH 侧渠道配置优先，hermes .env 兜底。

    新通道（2026-10-16 用户拍板「把飞书改成 DSH 里边通的那个飞书，别使 Hermes 那个
    飞书了」）：~/.dsh/dsh-im-connect/channels.json 里飞书渠道的 config.appId/appSecret
    —— 与 dsh-im-connect 插件共用同一套应用凭据，零额外维护。取不到（文件缺失或该渠道
    未配置）时回退 ~/.hermes/.env 的 FEISHU_* 键，故旧环境与既有测试桩不受影响。
    环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN / FEISHU_GATE_CHAT_ID
    覆盖一切（部署与联调用；仍是 _load_env 单一入口，测试照旧 patch 本函数）。
    """
    cfg = {}
    env_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    env_secret = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if env_id and env_secret:
        # 群号与 channels.json 分支同口径回落 GATE_CHAT_ID：preflight 修复文案教的
        # 「或设 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量」那条路，未设群号也能跑
        # （#125 review 补：旧写法留空 → 构造器必抛「确认门群未配置」）。
        cfg = {"FEISHU_APP_ID": env_id, "FEISHU_APP_SECRET": env_secret,
               "FEISHU_DOMAIN": (os.environ.get("FEISHU_DOMAIN") or "").strip(),
               "FEISHU_DAILY_NEWS_CHAT_ID": (os.environ.get("FEISHU_GATE_CHAT_ID")
                                             or GATE_CHAT_ID).strip()}
    else:
        chan = os.environ.get("FEISHU_CHANNEL") or DEFAULT_CHANNEL
        try:
            data = json.load(open(CHANNELS_JSON, encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 读不到即回退 hermes（保留旧口径）
            data = {}
        cc = (((data.get("channels") or {}).get(chan) or {}).get("config") or {})
        if (cc.get("appId") or "").strip() and (cc.get("appSecret") or "").strip():
            cfg = {"FEISHU_APP_ID": cc["appId"].strip(),
                   "FEISHU_APP_SECRET": cc["appSecret"].strip(),
                   "FEISHU_DOMAIN": (os.environ.get("FEISHU_DOMAIN") or "").strip(),
                   "FEISHU_DAILY_NEWS_CHAT_ID": (os.environ.get("FEISHU_GATE_CHAT_ID")
                                                 or GATE_CHAT_ID)}
    # 凭证源显式留痕（#125 review：#56 坑2 的症状正是「静默回落 hermes → 旧应用 +
    # 旧会话，改了没生效」，故三条取源路径各留一行，FEISHU_GATE_SILENT=1 可静音）。
    src = ""
    if env_id and env_secret:
        src = f"环境变量 FEISHU_APP_ID（应用 {env_id[:6]}…）"
    elif cfg.get("FEISHU_APP_ID"):
        src = f"{CHANNELS_JSON} 渠道 {chan}（应用 {cfg['FEISHU_APP_ID'][:6]}…）"
    if not cfg.get("FEISHU_APP_ID") and os.path.exists(HERMES_ENV):
        for line in open(HERMES_ENV, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k.startswith("FEISHU_"):
                cfg.setdefault(k, v.strip().strip('"').strip("'"))
        if cfg.get("FEISHU_APP_ID"):
            src = (f"⚠️ 旧口径回退 {HERMES_ENV}（DSH 源不可用：{CHANNELS_JSON} 渠道 "
                   f"{os.environ.get('FEISHU_CHANNEL') or DEFAULT_CHANNEL} 无 appId/appSecret）")
    if src:
        _glog("凭证源: " + src)
    return cfg


class FeishuGate:
    def __init__(self, chat_id=None, domain=None, dry=False):
        cfg = _load_env()
        self.app_id = cfg.get("FEISHU_APP_ID", "")
        self.app_secret = cfg.get("FEISHU_APP_SECRET", "")
        self.domain = self._norm_domain(domain or cfg.get("FEISHU_DOMAIN") or "")
        self.chat_id = chat_id or cfg.get("FEISHU_DAILY_NEWS_CHAT_ID", "")
        self.dry = dry  # dry 模式只打印不发送（driver --dry-run 用）
        if dry and not (self.app_id and self.app_secret and self.chat_id):
            # dry-run：凭证/群缺失不拦启动（确认门不参与 dry-run），只留痕
            _glog("⚠️ dry 模式：飞书凭证/群未配置，确认门将只打印跳过")
        if not (self.app_id and self.app_secret) and not dry:
            raise RuntimeError(f"FEISHU_APP_ID/SECRET 缺失（{CHANNELS_JSON} 或 {HERMES_ENV}）")
        if not self.chat_id and not dry:
            raise RuntimeError("FEISHU_GATE_CHAT_ID 缺失（确认门群未配置）")
        self._token = None
        self._token_exp = 0

    @staticmethod
    def _norm_domain(raw):
        """飞书域名可能是不带 scheme 的短值（如 feishu），归一化。"""
        raw = (raw or "").strip().rstrip("/")
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        if "larksuite" in raw or raw == "lark":
            return "https://open.larksuite.com"
        return "https://open.feishu.cn"

    # ── 基础 ────────────────────────────────────────────────────────────
    def token(self, force=False):
        """tenant_access_token；force=True 强制重取（跳过内存与进程外缓存）。"""
        if force:
            # 2026-10-17 事后：服务端可在 expire 之前判 token 无效（99991663）。
            # 重取必须同时清内存 + 进程外缓存——只清内存会被 TOKEN_CACHE 里同一枚
            # 失效 token 立刻喂回（文件 exp 仍在未来），重试必然再败。
            self._token, self._token_exp = None, 0
            try:
                if os.path.exists(TOKEN_CACHE):
                    os.remove(TOKEN_CACHE)
            except OSError:
                pass
        if not force and self._token and time.time() < self._token_exp - 120:
            return self._token
        # 进程外缓存（driver 长跑中重启模块也能复用）
        if not force and os.path.exists(TOKEN_CACHE):
            try:
                d = json.load(open(TOKEN_CACHE))
                if d.get("app_id") == self.app_id and time.time() < d.get("exp", 0) - 120:
                    self._token, self._token_exp = d["token"], d["exp"]
                    return self._token
            except Exception:
                pass
        body = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            f"{self.domain}/open-apis/auth/v3/tenant_access_token/internal",
            data=body, headers={"Content-Type": "application/json"})
        d = self._urlopen_json(req, timeout=15)
        if d.get("code") != 0:
            raise RuntimeError(f"tenant_access_token 失败: code={d.get('code')} msg={d.get('msg')}")
        self._token = d["tenant_access_token"]
        self._token_exp = time.time() + d.get("expire", 7200)
        json.dump({"app_id": self.app_id, "token": self._token, "exp": self._token_exp},
                  open(TOKEN_CACHE, "w"))
        return self._token

    @staticmethod
    def _urlopen_json(req, timeout=30, attempts=4):
        """网络层统一重试（2026-09-06 根治：SSL EOF/超时/连接重置等瞬断曾直接炸穿 driver 主循环）。
        指数退避 2/4/8s；HTTPError 带 JSON 体的按飞书业务码返回；耗尽后统一抛 RuntimeError。"""
        import socket
        import ssl
        last = None
        for i in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                try:
                    return json.load(e)
                except Exception:
                    return {"code": e.code, "msg": f"HTTP {e.code}"}
            except (urllib.error.URLError, ssl.SSLError, socket.timeout, ConnectionError,
                    OSError, ValueError) as e:  # ValueError: 空体/半包 JSON
                last = e
                if i < attempts - 1:
                    time.sleep(2 ** (i + 1))
        raise RuntimeError(f"飞书网络请求 {attempts} 次失败: {last}")

    def _api(self, method, path, body=None, headers=None, raw=False, timeout=30):
        # 2026-10-17 事故修复：99991663（token 失效）时重取 token 并重试一次。
        # 服务端可在 expire 之前判无效，本地缓存/exp 判断看不出来；不重试整扇门 abort。
        d = None
        for auth_retry in (0, 1):
            h = {"Authorization": f"Bearer {self.token(force=auth_retry == 1)}"}
            if headers:
                h.update(headers)
            data = None
            if body is not None:
                if raw:
                    data = body
                else:
                    h.setdefault("Content-Type", "application/json; charset=utf-8")
                    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(f"{self.domain}{path}", data=data,
                                         headers=h, method=method)
            d = self._urlopen_json(req, timeout=timeout)
            if d.get("code") in AUTH_ERR_CODES and auth_retry == 0:
                _glog(f"接口 {path.split('?')[0]} 返回 code={d.get('code')}"
                      f"（token 失效）→ 重取 tenant_access_token 后重试一次")
                continue
            return d
        return d

    # ── 发送 ────────────────────────────────────────────────────────────
    def _send(self, msg_type, content_str):
        if self.dry:
            print(f"[feishu_gate][dry] {msg_type}: {content_str[:120]}")
            return {"code": 0, "data": {"message_id": "dry"}}
        d = self._api("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id",
                      body={"receive_id": self.chat_id, "msg_type": msg_type,
                            "content": content_str})
        if d.get("code") != 0:
            print(f"[feishu_gate] 发送失败 code={d.get('code')} msg={d.get('msg')}")
        return d

    def send_text(self, text):
        return self._send("text", json.dumps({"text": text}, ensure_ascii=False))

    def send_image(self, image_path, strict=False):
        """图片两步：上传得 image_key → 发 image 消息。strict=True 时失败抛异常。"""
        if self.dry:
            print(f"[feishu_gate][dry] image: {image_path}")
            return {"code": 0}
        up = self._upload(image_path, "/open-apis/im/v1/images",
                          fields={"image_type": "message"}, file_field="image")
        key = up.get("data", {}).get("image_key")
        if not key:
            if strict:
                raise RuntimeError(f"飞书图片上传失败: {up}")
            print(f"[feishu_gate] 图片上传失败: {up}")
            return up
        return self._send("image", json.dumps({"image_key": key}))

    def send_file(self, file_path, strict=False):
        """文件两步：上传得 file_key → 发 file 消息。strict=True 时失败抛异常。"""
        if self.dry:
            print(f"[feishu_gate][dry] file: {file_path}")
            return {"code": 0}
        up = self._upload(file_path, "/open-apis/im/v1/files",
                          fields={"file_type": "stream", "file_name": os.path.basename(file_path)},
                          file_field="file")
        key = up.get("data", {}).get("file_key")
        if not key:
            if strict:
                raise RuntimeError(f"飞书文件上传失败: {up}")
            print(f"[feishu_gate] 文件上传失败: {up}")
            return up
        return self._send("file", json.dumps({"file_key": key}))

    def _upload(self, path, api, fields, file_field):
        boundary = f"----feishu_gate_{int(time.time()*1000)}"
        pre = b""
        for k, v in fields.items():
            pre += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        pre += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        post = f"\r\n--{boundary}--\r\n".encode()
        with open(path, "rb") as f:
            data = pre + f.read() + post
        return self._api("POST", api, body=data, raw=True, timeout=600,
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    @staticmethod
    def _size_h(path):
        """发送成功行附体积（KB/MB 档）；读不到就省略。"""
        try:
            n = os.path.getsize(path)
        except OSError:
            return ""
        return f" ({n / 1048576:.1f}MB)" if n >= 1048576 else f" ({n / 1024:.0f}KB)"

    def _sent_line(self, kind, name, d):
        """#49：发送成功即打一行（dry 也打，带 [dry] 标记）；code!=0 不打
        （失败走 _send 原有打印）。返回是否成功，供汇总行计数。"""
        if isinstance(d, dict) and d.get("code") == 0:
            _glog(f"✅ {kind}已发送: {name}", dry=self.dry)
            return 1
        return 0

    # ── 接收 ────────────────────────────────────────────────────────────
    def latest_user_texts(self, since_ms):
        """拉群里 since_ms（Unix 毫秒）之后 user 发的 text 消息，按时间升序。
        返回 [(msg_ts_ms, text)]；权限不足时抛 RuntimeError（driver 转为叫人）。"""
        # 飞书 start_time/end_time 只认 Unix 秒（传毫秒会把窗口起点推到未来 → 永远 0 条：
        # 2026-10-16 前 8 次确认门 timeout 的根因）。返回项 update_time 是毫秒（实测
        # 1792147468059），故出参仍保持 ms 口径，由本行做单位换算。
        d = self._api("GET", "/open-apis/im/v1/messages"
                      f"?container_id_type=chat&container_id={self.chat_id}"
                      f"&start_time={int(since_ms // 1000)}&end_time={int(time.time())}"
                      f"&page_size=50&sort_type=ByCreateTimeAsc")
        if d.get("code") != 0:
            hint = ("重取 tenant_access_token 后仍无效——检查应用凭证/密钥是否被轮换"
                    if d.get("code") in AUTH_ERR_CODES
                    else "检查 bot 是否有 im:message 历史 read 权限")
            raise RuntimeError(
                f"历史消息拉取失败 code={d.get('code')} msg={d.get('msg')}（{hint}）")
        out = []
        for item in d.get("data", {}).get("items", []) or []:
            if item.get("msg_type") != "text":
                continue
            if (item.get("sender") or {}).get("sender_type") == "app":
                continue  # 跳过 bot 自己（含本模块与 hermes 发的），只留 user 回复
            try:
                text = json.loads(item.get("body", {}).get("content", "{}")).get("text", "")
            except Exception:
                continue
            out.append((int(item.get("update_time", 0)), (text or "").strip()))
        return out

    def parse_shot_ids(self, text):
        """从回复文本解析段号（shot01/shot1/1、2、5-8/01 02 等）→ 去重排序的 shotNN 列表。
        区间尝试整段语义（#69）：越界/倒序区间（0-5、1-199、5-0）整段丢弃，
        端点不再被裸数字路径部分拾起；同句段外孤立合法数字照常拾起。"""
        t = text or ""
        ids = set()
        # 显式 shotNN / shotN（与 classify 探测共用 _SHOT_RE；v>=1：shot0/shot00 不成段）
        for m in _SHOT_RE.finditer(t):
            v = int(m.group(1))
            if v >= 1:
                ids.add(v)
        # 列举链补拾（2026-10-20 事故：'shot01/05/08/12/14/19' 只拾到 shot01）：
        # 紧跟 shotNN 之后、由 / 、 , 连接的裸数字并入同一批指认。
        for m in _SHOT_CHAIN_RE.finditer(t):
            for d in re.finditer(r"\d{1,2}", m.group(2)):
                v = int(d.group(0))
                if 1 <= v <= 99:
                    ids.add(v)
        # 「重生成 3、5、7」「1-4」这类裸数字（仅在文本含重生成/重来类词时才启用，
        # 避免误伤普通聊天数字）
        if any(w in t for w in REGEN_WORDS):
            # 区间尝试两侧取任意长数字串：合法（1<=a<=b<=99）整段展开，否则整段屏蔽
            masked = []
            for m in re.finditer(r"(?<!\d)(\d+)\s*[-~]\s*(\d+)(?!\d)", t):
                a, b = int(m.group(1)), int(m.group(2))
                if 1 <= a <= b <= 99:
                    ids.update(range(a, b + 1))
                else:
                    masked.append(m.span())
            for m in re.finditer(r"(?<![\d.])(\d{1,2})(?![\d.])", t):
                if any(s <= m.start() < e for s, e in masked):
                    continue  # 越界/倒序区间段内数字不拾（#69 候选2）
                v = int(m.group(1))
                if 1 <= v <= 99:
                    ids.add(v)
        return sorted(ids)

    def classify(self, text):
        """回复分类：pass / shot_regen / regen / flag（T17 #129 指认翻转）/ feedback。
        空文本按 feedback 处理。flag 判定树（指认语义优先于镜头级重做）：
          REGEN 词+段号 → shot_regen（显式重做指令）；REGEN 词+无段号+指认词 → flag
          （「重生成，画面翻转了」）；REGEN 词+无段号 → regen；裸 shotNN+指认词 → flag
          （「shot02 翻转了」）；裸 shotNN → shot_regen；指认词无段号 → flag
          （「第3段翻转了」，此前 feedback 叫人）；兜底 feedback。"""
        t = (text or "").strip()
        if not t:
            return "feedback"
        core = t.lower().strip("。！!，, .~～")
        if core in PASS_WORDS:
            return "pass"
        # 「横版通过 竖版通过」「都通过」这类带通过的组合：仅含通过词与空白/标点即 pass
        stripped = re.sub(r"[通过✅\s，,。/、和与都横竖版]", "", t)
        # 「通过 批准」这类多通过词组合（0913 实证误判 feedback）：白名单词剔除后无剩余 → pass
        for w in ("批准", "同意"):
            stripped = stripped.replace(w, "")
        if not stripped and ("通过" in t or "✅" in t or "批准" in t or "同意" in t):
            return "pass"
        _flag_hit = any(w in t for w in FLAG_WORDS)
        if any(w in t for w in REGEN_WORDS):
            # 「重生成 shot02」等带段号 → 镜头级重生成；纯「重生成」→ 兼容旧封面重做
            if self.parse_shot_ids(t):
                return "shot_regen"
            # 「重生成，画面翻转了」：重做意图+指认翻转 → 指认回路（段号由 driver 侧兜底解析）
            if _flag_hit:
                return "flag"
            return "regen"
        # 裸 shotNN（#69 候选1收紧）：shot 前缀即镜头指称——判定与 gate_confirm 取
        # shots 同走 parse_shot_ids，判 shot_regen 时 shots 必非空（shot0/shot100
        # 不成段 → feedback 叫人）；裸数字仍守卫——无 REGEN 词不触发（防误伤普通聊天）
        if self.parse_shot_ids(t):
            # 「shot02 翻转了」：指认语义优先于镜头级重做（存证+真值回填后照样重烧）
            return "flag" if _flag_hit else "shot_regen"
        # 「第3段翻转了」：无段号指认（此前落 feedback 叫人，#129 AC1 目标场景）
        if _flag_hit:
            return "flag"
        return "feedback"

    def _decision(self, status, reply, shots=None):
        """Decision（票8 #67 Q1）：门内政策表消化后的执行指令 + 原始状态。
        shots 带 shotNN 列表（shot_regen 必带；flag 解析得到段号时也带，T17 #129）；
        regen 的 retry 不带键——driver 侧 shots 空表 = 封面重做（Q3 统一语义）。"""
        out = {"action": _ACTION[status], "status": status, "reply": reply}
        if shots is not None:
            out["shots"] = shots
        return out

    def gate_confirm(self, caption=None, image_path=None, file_path=None,
                     since_ms=None, timeout_s=3600, poll_s=10):
        """确认门：发内容 → 轮询回复 → Decision（票8 #67）。
        image_path/file_path 可传 str 或 list（多图/多文件）。
        action: pass（放行）/ retry（regen_fn 重做后重新送审）/ abort（叫人退出）；
        status: pass / shot_regen / regen / feedback / timeout / error（原始 6 值）。
        传输异常不经 Decision：strict 上传失败直接 RuntimeError 抛出（run_stage 重试接住）。
        #49：每个包发送成功各打一行、随后一行轮询起点/截止——监控者据此分辨
        「在传包」还是「已进轮询」，门等待期在 driver 日志全程可见。"""
        images = [image_path] if isinstance(image_path, str) else (image_path or [])
        files = [file_path] if isinstance(file_path, str) else (file_path or [])
        total = len(images) + len(files) + (1 if caption else 0)
        no, ok = 0, 0  # no=第几个包（按位）；ok=真正 code==0 的数（汇总不夸大）
        for p in images:
            d = self.send_image(p, strict=True)
            no += 1
            ok += self._sent_line(f"图片 {no}/{total}", os.path.basename(p) + self._size_h(p), d)
        for p in files:
            d = self.send_file(p, strict=True)
            no += 1
            ok += self._sent_line(f"文件 {no}/{total}", os.path.basename(p) + self._size_h(p), d)
        if caption:
            d = self.send_text(caption)
            no += 1
            ok += self._sent_line(f"正文 {no}/{total}", f"{len(caption)} 字", d)
        start = since_ms if since_ms is not None else int(time.time() * 1000)
        deadline = time.time() + timeout_s
        _glog(f"审核包发送完毕（{ok}/{total} 成功），开始轮询回复："
              f"起 {time.strftime('%H:%M:%S')}、"
              f"{time.strftime('%H:%M:%S', time.localtime(start / 1000))} 之后的新回复计入、"
              f"截止 {time.strftime('%H:%M:%S', time.localtime(deadline))}"
              f"（每 {poll_s:g}s 一查，期间静默属正常）", dry=self.dry)
        fail_streak = 0
        fail_first = 0.0
        while time.time() < deadline:
            time.sleep(poll_s)
            try:
                msgs = self.latest_user_texts(start)
                fail_streak = 0
            except Exception as e:  # noqa: BLE001
                # 瞬断容错（2026-09-06 根治：网络抖动曾直接放弃整扇门并触发重发整包）
                fail_streak += 1
                if fail_streak == 1:
                    fail_first = time.time()
                # 每次失败都落日志（2026-10-17 事故：旧实现只在第 20 次打一行，
                # token 失效 65 分钟内日志全静默，事后无法定位失败起点与触发码）
                _glog(f"轮询第 {fail_streak} 次失败（连续 {time.time() - fail_first:.0f}s）："
                      f"{str(e)[:140]}", dry=self.dry)
                if fail_streak >= POLL_FAIL_MAX:
                    _glog(f"轮询连续失败 {fail_streak} 次 / "
                          f"{time.time() - fail_first:.0f}s → error（driver 叫人）")
                    return self._decision("error", str(e)[:200])
                continue
            for ts, text in msgs:
                if not text:
                    continue
                status = self.classify(text)
                _glog(f"收到回复 {text[:60]!r} → 判定 {status}，轮询结束")
                shots = None
                if status in ("shot_regen", "flag"):  # flag 带段号时随 Decision 下发（T17 #129）
                    shots = [f"shot{int(v):02d}" for v in self.parse_shot_ids(text)]
                return self._decision(status, text, shots)
        _glog("已到轮询截止仍无回复 → timeout（driver 叫人）")
        return self._decision("timeout", "")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    g = FeishuGate()
    if cmd == "send":
        d = g.send_text(sys.argv[2])
        print(json.dumps(d, ensure_ascii=False)[:300])
    elif cmd == "image":
        path = sys.argv[2]
        cap = sys.argv[sys.argv.index("--caption") + 1] if "--caption" in sys.argv else None
        g.send_image(path)
        if cap:
            g.send_text(cap)
    elif cmd == "file":
        g.send_file(sys.argv[2])
    elif cmd == "poll":
        mins = float(sys.argv[sys.argv.index("--since-min") + 1]) if "--since-min" in sys.argv else 10
        msgs = g.latest_user_texts(int((time.time() - mins * 60) * 1000))
        print(f"最近 {mins} 分钟 {len(msgs)} 条 user 消息：")
        for ts, text in msgs:
            print(f"  {time.strftime('%H:%M:%S', time.localtime(ts/1000))} {text[:60]} [{g.classify(text)}]")
    elif cmd == "gate":
        # CLI 联调：发一条消息然后等回复（--timeout 秒）
        to = float(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 120
        g.send_text("[feishu_gate 联调] 回复「通过」验证轮询链路")
        r = g.gate_confirm(caption=None, since_ms=int(time.time() * 1000), timeout_s=to, poll_s=5)
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
