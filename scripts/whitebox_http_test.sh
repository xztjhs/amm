#!/bin/bash
BASE="http://192.168.100.245:60006"
P=0; F=0
ck(){ if [ "$2" = "1" ]; then P=$((P+1)); echo "  ✅ $1"; else F=$((F+1)); echo "  ❌ $1 $3"; fi; }
H=$(curl -s -m 10 "$BASE/api/health")
ck "API /api/health ok" "$(echo "$H" | python3 -c "import sys,json;print('1' if json.load(sys.stdin).get('status')=='ok' else '0')")"
S=$(curl -s -m 20 "$BASE/api/system")
ck "API /api/system 含gpus" "$(echo "$S" | python3 -c "import sys,json;print('1' if json.load(sys.stdin).get('gpus') else '0')")"
ck "API GPU 含 running_processes 字段" "$(echo "$S" | grep -c 'running_processes')"
I=$(curl -s -m 30 "$BASE/api/instances")
ck "API /api/instances 9模型" "$(echo "$I" | python3 -c "import sys,json;print('1' if len(json.load(sys.stdin))==9 else '0')")"
Q=$(curl -s -m 10 "$BASE/api/quantize/types")
ck "API 量化含q4_k_m/fp16/bf16" "$(echo "$Q" | python3 -c "import sys,json;d=json.load(sys.stdin).get('quant_types',{});print('1' if {'q4_k_m','fp16','bf16'}<=set(d) else '0')")"
FS=$(curl -s -m 10 "$BASE/api/fs/list?path=")
ck "API fs/list 浏览/models" "$(echo "$FS" | python3 -c "import sys,json;print('1' if len(json.load(sys.stdin).get('dirs',[]))>=3 else '0')")"
VM=$(curl -s -m 10 "$BASE/v1/models")
ck "API /v1/models>0" "$(echo "$VM" | python3 -c "import sys,json;print('1' if len(json.load(sys.stdin).get('data',[]))>0 else '0')")"
echo; echo "📊 HTTP-API: PASS=$P FAIL=$F"
