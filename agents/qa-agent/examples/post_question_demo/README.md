# POST Question Demo

使用 Python 标准库向以下接口发送 `POST` 请求：

`http://172.20.10.14:8000/api/v1/qa/questions`

默认请求体：

```json
{"id": "xxx", "question": "今天天气如何"}
```

运行方式：

```bash
cd /Users/wangxinhao/muti-agent-offline-system/examples/post_question_demo
python3 send_question.py
```

自定义参数：

```bash
python3 send_question.py --id test-001 --question "今天天气如何"
```
