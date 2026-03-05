# M06_通算_災害					
# LOAD条件: M03=LOAD AND (入院数>1 OR 既払に災害入院あり) / 挿入: ★M06_CHK / 復帰: ★M07_CHK					
step_id	question	yes_next	no_next	yes_effect	no_effect
S11_PRE	複数傷病あり、通算のため因果関係確認が必要か？[AI]	S11_PRE_STOP	S11_CHK_D	-	-
S11_PRE_STOP	【STOP】因果関係判断が必要。査定者は過去医務を検索し因果関係を入力	S11_PRE_INPUT	-	-	-
S11_PRE_INPUT	【査定者入力待ち】因果関係or「医務照会」を入力	S11_CHK_D	-	因果関係=査定者入力値	入力待ち
S11_CHK_D	災害入院支払対象が複数の別入院として存在？(M05同一入院内併発は対象外)	S11_1_2	→★M07_CHK	複数あり	-
S11_1_2	各入院の直接原因の不慮の事故は同一か？[AI]	S11_1_3	→★M07_CHK	同一事故	-
S11_1_3	各入院開始日は事故日から180日以内か？	S11_1_4	→★M07_CHK	180日以内	-
S11_1_4	災害入院を1回の入院として通算記録	→★M07_CHK	→★M07_CHK	災害通算=適用	=