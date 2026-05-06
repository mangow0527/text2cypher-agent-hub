from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Tuple

import requests
from requests import Session
from loguru import logger


Triple = Tuple[str, str, str]


def tugraph_connection_from_env() -> Tuple[str, str, str, str]:
    """
    从环境变量读取 TuGraph 连接信息（进程级配置，单处维护）。

    - ``TUGRAPH_BASE_URL``：REST 根地址，默认 ``http://120.26.62.254:7070``
    - ``TUGRAPH_USER``：默认 ``admin``
    - ``TUGRAPH_PASSWORD``：默认 ``73@TuGraph``
    - ``TUGRAPH_GRAPH``：子图名，默认 ``network_schema_v10``
    """
    base = (os.getenv("TUGRAPH_BASE_URL") or "http://118.196.92.128:7070").strip().rstrip("/")
    user = (os.getenv("TUGRAPH_USER") or "admin").strip()
    password = os.getenv("TUGRAPH_PASSWORD") or "73@TuGraph"
    graph = (os.getenv("TUGRAPH_GRAPH") or "network_schema_v10").strip() or "network_schema_v10"
    return base, user, password, graph


class TuGraphHttpOps:
    """
    使用 TuGraph RESTful API Legacy 进行数据写入的封装（HTTP 方式）。

    无参构造 ``TuGraphHttpOps()`` 时，连接信息完全由 :func:`tugraph_connection_from_env` 提供。
    若传入 ``base_url`` / ``user`` / ``password`` / ``graph`` 中任意一项，则仅未传的字段回退到环境变量。

    文档参考：
    - 登录接口 `/login`
    - Cypher 接口 `/cypher`（POST，字段：graph, script, parameters）
    见官方文档：[TuGraph RESTful API Legacy](https://tugraph-family.github.io/tugraph-db/en/4.5.2/client-tools/restful-api-legacy)
    """

    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        graph: str | None = None,
    ) -> None:
        eb, eu, ep, eg = tugraph_connection_from_env()
        self.base_url = (base_url if base_url is not None else eb).strip().rstrip("/")
        self.user = (user if user is not None else eu).strip()
        self.password = password if password is not None else ep
        self.graph = (graph if graph is not None else eg).strip() or "default"
        self.jwt: str | None = None
        self._session: Session = requests.Session()
        # HTTP 重试参数（主要应对连接拒绝、瞬时网络抖动）
        self.http_timeout_s = int(os.getenv("TUGRAPH_HTTP_TIMEOUT_S", "30"))
        self.http_retries = max(0, int(os.getenv("TUGRAPH_HTTP_RETRIES", "2")))
        self.http_backoff_base_s = float(os.getenv("TUGRAPH_HTTP_BACKOFF_BASE_S", "0.5"))

        self.login()

    # --- 辅助 HTTP 封装 ---

    def _headers(self, with_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Accept": "application/json; charset=UTF-8",
            "Content-Type": "application/json; charset=UTF-8",
        }
        if with_auth and self.jwt:
            headers["Authorization"] = f"Bearer {self.jwt}"
        return headers

    def _get(self, path: str) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self._request_with_retry("GET", url, headers=self._headers())

    def _post(self, path: str, body: Dict[str, Any], auth: bool = True) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self._request_with_retry(
            "POST",
            url,
            headers=self._headers(with_auth=auth),
            data=json.dumps(body),
        )

    def _delete(self, path: str) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self._request_with_retry("DELETE", url, headers=self._headers())

    def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """
        统一 HTTP 请求重试：
        - 仅对连接类异常（ConnectionError / Timeout）重试；
        - 认证/业务错误（4xx/5xx）不在此层重试，交由上层按状态码处理。
        """
        retries = self.http_retries
        timeout_s = self.http_timeout_s
        base = self.http_backoff_base_s
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._session.request(method=method, url=url, timeout=timeout_s, **kwargs)
                # 预读取响应体，确保连接可及时归还到连接池，避免长时间占用 socket。
                _ = resp.content
                return resp
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_exc = e
                if attempt >= retries:
                    break
                delay = base * (2**attempt)
                logger.warning(
                    "TuGraphHttpOps %s %s failed (%s), retry %s/%s after %.2fs",
                    method,
                    url,
                    type(e).__name__,
                    attempt + 1,
                    retries,
                    delay,
                )
                time.sleep(delay)
        assert last_exc is not None
        msg = str(last_exc)
        low = msg.lower()
        if "10061" in msg or "actively refused" in low or "target machine actively refused" in low:
            logger.error("TuGraph connection refused: %s %s err=%s", method, url, msg)
        elif "timed out" in low:
            logger.error("TuGraph request timeout: %s %s err=%s", method, url, msg)
        else:
            logger.error("TuGraph request network error: %s %s err=%s", method, url, msg)
        raise last_exc

    def close(self) -> None:
        """显式关闭底层 HTTP Session（释放连接池资源）。"""
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> "TuGraphHttpOps":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- 基础 HTTP 封装 ---

    def login(self) -> None:
        """
        调用 `/login` 获取 JWT，后续所有请求带上 Authorization。
        """
        payload = {"user": self.user, "password": self.password}
        logger.info(f"TuGraphHttpOps 登录 {self.base_url}/login，user={self.user}")
        resp = self._post("/login", payload, auth=False)
        resp.raise_for_status()
        data = resp.json()
        self.jwt = data["jwt"]
        logger.info("TuGraphHttpOps 登录成功，已获取 JWT")

    def import_schema(self, schema_description: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 Legacy API /db/{graph}/schema/text 创建图模型。
        :param schema_description: 符合 TuGraph schema 格式（如 {\"schema\": [...]}），
            与 /import_schema 的 description 结构一致；会以 JSON 字符串形式传入。
        :return: 服务端响应 JSON
        """
        # Legacy API 要求 URI 为 /db/{graph_name}/schema/text，且 description 为 JSON 字符串
        path = f"/db/{self.graph}/schema/text"
        body: Dict[str, Any] = {
            "description": json.dumps(schema_description, ensure_ascii=False),
        }
        logger.info(f"TuGraphHttpOps 调用 {path}, graph={self.graph}")
        resp = self._post(path, body)
        if resp.status_code != 200:
            logger.error(f"{path} HTTP {resp.status_code}, body: {resp.text}")
            resp.raise_for_status()
        data = resp.json()
        logger.info("TuGraphHttpOps import_schema 成功")
        return data

    def call_cypher(self, script: str, parameters: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """
        通过 `/cypher` 接口执行 Cypher。

        :param script: Cypher 语句
        :param parameters: 参数（遵循文档：parameters 是一个对象，key 一般以 $param1 形式出现）
        :return: TuGraph 的完整响应 JSON（包含 elapsed/header/result/size 等）
        """
        url = f"{self.base_url}/cypher"
        body: Dict[str, Any] = {
            "graph": self.graph,
            # 文档存在 `cypher` / `script` 两种写法，这里同时提供，兼容不同版本
            "cypher": script,
            "script": script,
        }
        if parameters:
            # TuGraph Legacy 要求 Cypher 中的 $id 对应 parameters 里的键 "$id"（必须带 $）
            param_payload: Dict[str, Any] = {}
            for k, v in parameters.items():
                key = k if k.startswith("$") else f"${k}"
                param_payload[key] = v
            body["parameters"] = param_payload

        logger.info(f"TuGraphHttpOps 调用 /cypher, graph={self.graph}, script 前 100 字符: {script[:100]}...")
        resp = self._post("/cypher", body)
        if resp.status_code == 401:
            logger.warning("/cypher HTTP 401, refresh TuGraph JWT and retry once")
            self.login()
            resp = self._post("/cypher", body)
        if resp.status_code != 200:
            logger.error(f"/cypher HTTP {resp.status_code}, body: {resp.text}")
            resp.raise_for_status()
        data = resp.json()
        logger.info(f"/cypher 返回 size={data.get('size')}, elapsed={data.get('elapsed')}")
        return data

    # --- Label 确保存在 ---

    def _list_labels(self) -> Dict[str, Any]:
        """
        调用 /db/{graph}/label，返回当前子图的点/边标签。
        """
        path = f"/db/{self.graph}/label"
        resp = self._get(path)
        resp.raise_for_status()
        return resp.json()

    def _ensure_vertex_label(self, label: str) -> None:
        labels = self._list_labels()
        vertices = labels.get("vertex", [])
        if label in vertices:
            return
        logger.info(f"顶点标签 {label} 不存在，准备创建...")
        # 为了更通用，label schema 只声明一个主键字段 name，其他字段可在导入 schema 时扩展
        body = {
            "name": label,
            "fields": [
                {"name": "name", "type": "string", "optional": False},
            ],
            "is_vertex": True,
            "primary": "name",
        }
        resp = self._post(f"/db/{self.graph}/label", body)
        resp.raise_for_status()
        logger.info(f"顶点标签 {label} 创建成功")

    def _ensure_edge_label(self, label: str, from_label: str, to_label: str) -> None:
        labels = self._list_labels()
        edges = labels.get("edge", [])
        if label in edges:
            return
        logger.info(f"边标签 {label} 不存在，准备创建...")
        body = {
            "name": label,
            "fields": [],
            "is_vertex": False,
            "edge_constraints": [[from_label, to_label]],
        }
        resp = self._post(f"/db/{self.graph}/label", body)
        resp.raise_for_status()
        logger.info(f"边标签 {label} 创建成功")

    # --- 子图管理 ---

    def _list_subgraphs(self) -> Dict[str, Any]:
        """
        调用 /db，返回当前实例下的所有子图配置。
        """
        resp = self._get("/db")
        resp.raise_for_status()
        return resp.json()

    def ensure_subgraph_exists(self, graph_name: str | None = None) -> None:
        """
        确保子图存在；若不存在则按默认配置创建。

        - GET /db 列出所有子图
        - 若 name 不在其中，则 POST /db 创建 {\"name\": name, \"config\": {...}}
        """
        name = graph_name or self.graph
        subgraphs = self._list_subgraphs()
        if name in subgraphs:
            return

        logger.info(f"子图 {name} 不存在，准备创建...")
        body = {
            "name": name,
            "config": {
                "max_size_GB": 2048,
                "async": False,
            },
        }
        resp = self._post("/db", body)
        resp.raise_for_status()
        logger.info(f"子图 {name} 创建成功")

    # --- 子图管理 ---

    def delete_subgraph(self, graph_name: str | None = None) -> None:
        """
        删除子图。

        对应 RESTful API Legacy 文档中的：
        - DELETE /db/{graph_name}
        """
        name = graph_name or self.graph
        logger.info(f"删除子图: {name}")
        resp = self._delete(f"/db/{name}")
        if resp.status_code != 200:
            logger.error(f"删除子图 {name} 失败，HTTP {resp.status_code}, body: {resp.text}")
            resp.raise_for_status()
        logger.info(f"子图 {name} 删除成功")

    # --- 三元组写入 ---

    def insert_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        subject_label: str = "Entity",
        object_label: str = "Entity",
        rel_type: str | None = None,
    ) -> Dict[str, Any]:
        """
        插入一个三元组 (subject, predicate, object) 到 TuGraph。

        - 默认使用 Person 作为节点标签（对应官方示例库）
        - 默认关系类型为 rel_type（若未指定，则由 predicate 转换为大写并做简单清洗）
        - 使用 /cypher 接口执行 MERGE 语句
        """
        if rel_type is None:
            rel_type = "".join(c if c.isalnum() else "_" for c in predicate).upper() or "REL"

        # 确保子图存在
        self.ensure_subgraph_exists()

        # 确保点/边标签存在（若不存在则自动创建）
        self._ensure_vertex_label(subject_label)
        if object_label != subject_label:
            self._ensure_vertex_label(object_label)
        self._ensure_edge_label(rel_type, subject_label, object_label)

        # 为简化兼容性，这里直接将值内联到 Cypher 中，避免 REST Legacy 参数传递差异
        def esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace("'", "\\'")

        s_val = esc(subject)
        o_val = esc(obj)

        script = f"""
        MERGE (s:{subject_label} {{name:'{s_val}'}})
        MERGE (o:{object_label} {{name:'{o_val}'}})
        MERGE (s)-[r:{rel_type}]->(o)
        RETURN s.name AS subject, type(r) AS predicate, o.name AS object
        """
        return self.call_cypher(script)

    def insert_triples(
        self,
        triples: Iterable[Triple],
        subject_label: str = "Person",
        object_label: str = "Person",
        rel_type: str | None = None,
    ) -> List[Dict[str, Any]]:
        """批量插入若干三元组，逐个调用 insert_triple。"""
        results: List[Dict[str, Any]] = []
        for s, p, o in triples:
            res = self.insert_triple(
                subject=s,
                predicate=p,
                obj=o,
                subject_label=subject_label,
                object_label=object_label,
                rel_type=rel_type,
            )
            results.append(res)
        return results


if __name__ == "__main__":
    """
    测试样例代码：
    - 使用提供的 admin / 密码 登录 REST 接口
    - 调用 /cypher 向 default 子图写入三元组 ("zhangsan","HAS_CHILD","child")
    - 再查询验证
    """
    # 默认使用环境变量 TUGRAPH_*；也可显式传入覆盖
    ops = TuGraphHttpOps()

    triple = ("zhangsan", "HAS_CHILD", "child")
    print("写入三元组:", triple)
    insert_res = ops.insert_triple(*triple)
    print("写入返回:", json.dumps(insert_res, ensure_ascii=False, indent=2))

    # 再查一遍确认
    check_cypher = """
    MATCH (s:Entity {name:'zhangsan'})-[r:HAS_CHILD]->(o:Entity {name:'child'})
    RETURN s.name AS subject, type(r) AS predicate, o.name AS object
    """
    check = ops.call_cypher(check_cypher)
    print("验证查询结果:", json.dumps(check, ensure_ascii=False, indent=2))
