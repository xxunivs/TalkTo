#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# cognee 환경 패치 스크립트
#
# cognee-mcp 컨테이너 환경에 필요한 패치를 적용합니다.
# cognee 컨테이너가 재시작/재생성될 때마다 다시 실행해야 합니다.
#
# 사용법:
#   ./scripts/patch_cognee.sh
# =============================================================================

echo "=== 1. cognee .env.docker: instructor mode 패치 ==="
COGNEE_ENV="/home/commit/cognee-mcp/.env.docker"
if [ -f "$COGNEE_ENV" ]; then
    sed -i 's/LLM_INSTRUCTOR_MODE=json_mode/LLM_INSTRUCTOR_MODE=markdown_json_mode/' "$COGNEE_ENV"
    echo "  .env.docker → markdown_json_mode"
else
    echo "  WARNING: $COGNEE_ENV 파일 없음"
fi

echo ""
echo "=== 2. cognee-api: OpenAI adapter 패치 (instructor mode 강제 적용) ==="
# gpt-5 조건문을 제거하여 항상 설정된 instructor_mode 사용
docker exec cognee-api sed -i \
    's/if "gpt-5" in model:/if True:  # Always use configured instructor_mode/' \
    /usr/local/lib/python3.12/site-packages/cognee/infrastructure/llm/structured_output_framework/litellm_instructor/llm/openai/adapter.py 2>/dev/null \
    && echo "  adapter.py → if True: 패치 완료" \
    || echo "  WARNING: cognee-api 컨테이너 접근 실패"

# pycache 정리
docker exec cognee-api find /usr/local/lib/python3.12/site-packages/cognee/ \
    -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
echo "  pycache 정리 완료"

echo ""
echo "=== 3. cognee-frontend: 빠진 파일 생성 ==="

# src/lib/adaptCogneeGraphData.ts
docker exec cognee-frontend mkdir -p /app/src/lib 2>/dev/null || true
docker exec cognee-frontend sh -c 'cat > /app/src/lib/adaptCogneeGraphData.ts << '\''ENDTS'\''
import { CogneeGraphResponse } from "@/types/CogneeAPI";
import { Edge, Node } from "@/ui/rendering/graph/types";

export function validateCogneeGraphResponse(data: any): data is CogneeGraphResponse {
  return data && typeof data === "object" && Array.isArray(data.nodes) && Array.isArray(data.edges);
}

export function adaptCogneeGraphData(data: CogneeGraphResponse): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = data.nodes.map((n) => ({
    id: n.id,
    label: n.label || n.properties?.name || n.id,
    type: n.type || "unknown",
  }));
  const edges: Edge[] = data.edges.map((e, i) => ({
    id: `edge-${i}`,
    label: e.label || "",
    source: e.source,
    target: e.target,
  }));
  return { nodes, edges };
}
ENDTS' && echo "  adaptCogneeGraphData.ts 생성 완료"

# src/lib/inferNodeSets.ts
docker exec cognee-frontend sh -c 'cat > /app/src/lib/inferNodeSets.ts << '\''ENDTS'\''
import { Node, Edge } from "@/ui/rendering/graph/types";
import { NodeSet, RetrievalResult } from "@/types/NodeSet";

interface InferOptions { minSetSize?: number; maxSets?: number; }
interface InferResult { typeAttributes: Map<string, number>; inferredSets: NodeSet[]; }

export function inferNodeSets(nodes: Node[], edges: Edge[], options: InferOptions = {}): InferResult {
  const { minSetSize = 1, maxSets = 15 } = options;
  const typeAttributes = new Map<string, number>();
  nodes.forEach((n) => { const t = n.type || "unknown"; typeAttributes.set(t, (typeAttributes.get(t) || 0) + 1); });
  const inferredSets: NodeSet[] = [];
  typeAttributes.forEach((count, type) => {
    if (count >= minSetSize) {
      const nodeIds = nodes.filter((n) => n.type === type).map((n) => n.id);
      inferredSets.push({ id: `set-${type}`, name: type, nodeIds, size: nodeIds.length, definition: `Nodes of type "${type}"`, stability: "stable", source: "model-inferred", lastUpdated: new Date(), confidence: 1, cohesion: 0.8 });
    }
  });
  inferredSets.sort((a, b) => b.size - a.size);
  return { typeAttributes, inferredSets: inferredSets.slice(0, maxSets) };
}

export function mockRetrievalSearch(query: string, nodes: Node[], sets: NodeSet[]): RetrievalResult[] {
  const q = query.toLowerCase();
  const results: RetrievalResult[] = [];
  nodes.forEach((n) => { if (n.label && n.label.toLowerCase().includes(q)) results.push({ type: "node", nodeId: n.id, nodeLabel: n.label, why: `Label contains "${query}"`, similarityScore: 0.9, confidence: 0.9, signals: [{ name: "label-match", weight: 0.9, value: n.label }] }); });
  sets.forEach((s) => { if (s.name.toLowerCase().includes(q)) results.push({ type: "nodeSet", nodeSet: s, why: `Set name matches "${query}"`, similarityScore: 0.8, confidence: 0.8, signals: [{ name: "set-name-match", weight: 0.8, value: s.name }] }); });
  return results.sort((a, b) => b.similarityScore - a.similarityScore).slice(0, 10);
}
ENDTS' && echo "  inferNodeSets.ts 생성 완료"

echo ""
echo "=== 4. cognee-frontend: npm 패키지 설치 ==="
docker exec cognee-frontend npm install ngraph.forcelayout ngraph.graph 2>/dev/null | tail -1 \
    && echo "  ngraph 패키지 설치 완료" \
    || echo "  WARNING: npm install 실패"

echo ""
echo "=== 5. cognee-frontend: Entity 라벨 표시 + 폰트 크기 조정 ==="
# Entity 노드에도 라벨 표시
docker exec cognee-frontend sed -i \
    's/if (node.type === "EntityType") {/if (node.type === "EntityType") {\n        const label = createLabel(node.label, 6, "#888888");\n        entityTypeLabels.push([node.id, label]);\n      } else if (node.type === "Entity") {/' \
    /app/src/ui/rendering/animate.ts 2>/dev/null \
    && echo "  Entity 라벨 표시 패치 완료" \
    || echo "  WARNING: animate.ts 패치 실패"

# 폰트 크기 축소
docker exec cognee-frontend sh -c 'cat > /app/src/ui/rendering/meshes/createLabel.ts << '\''ENDTS'\''
import { Color } from "three";
import { Text } from "troika-three-text";
const LABEL_FONT_SIZE = 8;
export default function createLabel(text = "", fontSize = LABEL_FONT_SIZE, color = "#ffffff", opacity = 1.0): Text {
  const label = new Text();
  label.text = text; label.fontSize = fontSize;
  label.color = new Color(color); label.strokeColor = new Color(color);
  label.outlineWidth = 1.5; label.outlineColor = new Color("#000000"); label.outlineOpacity = 0.6;
  label.anchorX = "center"; label.anchorY = "middle";
  label.visible = true; label.frustumCulled = false; label.renderOrder = 5; label.maxWidth = 150;
  label.sync(); return label;
}
ENDTS' && echo "  createLabel.ts 폰트 크기 조정 완료"

echo ""
echo "=== 6. 컨테이너 재시작 ==="
docker restart cognee-api cognee-frontend 2>/dev/null \
    && echo "  cognee-api, cognee-frontend 재시작 완료" \
    || echo "  WARNING: 재시작 실패"

echo ""
echo "=== 패치 완료 ==="
echo "cognee-api와 cognee-frontend가 재시작되었습니다."
echo "10초 후 접속 가능: http://localhost:3030"
