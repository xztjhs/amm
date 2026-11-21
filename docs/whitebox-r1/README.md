# 白盒 E2E 测试产物

- `merged-r1.json` 第一轮 160 例结果（154 通过 / 4 失败 / 2 跳过）
- `merged-r2.json` 第二轮 176 例结果（175 通过 / 1 跳过 / 0 失败）
- `shots/` 失败与关键步骤截图
- `r2-*.json` 第二轮各层原始结果

重跑：
```bash
python3 scripts/whitebox/run.py --base http://<host>:60009 --layer ALL --destructive
```
