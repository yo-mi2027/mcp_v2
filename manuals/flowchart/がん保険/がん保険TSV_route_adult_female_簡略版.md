# 概要: がん保険 成人病/女性特約ルート（簡略版）。入院+手術ルート完了後に実行。

# TSVフロー
step_id	question	yes_next	no_next	yes_effect	no_effect
S10_LOOP	支払対象の入院・手術があるか？（日付の古い順）	S10_2	ROUTE_END		対象なし→ルート完了
S10_2	特約の対象となる疾病・治療内容か？	S10_OK	S10_X		
S10_OK	特約給付金を支払対象として記録	S10_NEXT	S10_NEXT	特約=支払対象	
S10_X	特約給付金を対象外として記録	S10_NEXT	S10_NEXT	特約=対象外	
S10_NEXT	未判定の入院・手術が残っているか？	S10_LOOP	ROUTE_END	次の入院・手術へ	完了→ルート完了
ROUTE_END	【成人病/女性特約ルート完了】結果を最終算定へ引渡し	-	-	成人病/女性特約ルート=完了	成人病/女性特約ルート=完了
