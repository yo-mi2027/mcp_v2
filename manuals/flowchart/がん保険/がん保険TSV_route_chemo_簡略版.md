# 概要: がん保険 抗がん剤ルート（簡略版）。

# TSVフロー
step_id	question	yes_next	no_next	yes_effect	no_effect
S8_LOOP	判定対象の抗がん剤治療月があるか？（古い順）	S8_2	ROUTE_END		治療月なし→ルート完了
S8_2	厚労省承認済の抗がん剤による治療か？	S8_3	S8_X		
S8_3	公的医療保険対象の治療（入院/通院/処方）か？	S8_4	S8_X		
S8_4	通算支払限度月数（120ヶ月）未満か？	S8_OK	S8_X		
S8_OK	この月を支払対象として記録	S8_NEXT	S8_NEXT	支払対象	
S8_X	この月を対象外として記録	S8_NEXT	S8_NEXT	対象外	
S8_NEXT	未判定の治療月が残っているか？	S8_LOOP	ROUTE_END	次の月へ	完了→ルート完了
ROUTE_END	【抗がん剤ルート完了】結果を最終算定へ引渡し	-	-	抗がん剤ルート=完了	抗がん剤ルート=完了
