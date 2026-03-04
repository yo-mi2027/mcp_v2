# 概要: がん保険 先進医療ルート（簡略版）。

# TSVフロー
step_id	question	yes_next	no_next	yes_effect	no_effect
S9_LOOP	判定対象の先進医療があるか？（受療日の古い順）	S9_2	ROUTE_END		先進医療なし→ルート完了
S9_2	受療時点で認定済の技術・施設か？	S9_3	S9_X		
S9_3	待機期間（がん90日）等の要件を満たすか？	S9_4	S9_X		
S9_4	通算支払限度額未満か？	S9_OK	S9_X		
S9_OK	支払対象として記録	S9_NEXT	S9_NEXT	支払対象	
S9_X	対象外として記録	S9_NEXT	S9_NEXT	対象外	
S9_NEXT	未判定の先進医療が残っているか？	S9_LOOP	ROUTE_END	次の先進医療へ	完了→ルート完了
ROUTE_END	【先進医療ルート完了】結果を最終算定へ引渡し	-	-	先進医療ルート=完了	先進医療ルート=完了
