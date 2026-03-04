# 01a_factcheck（入院ありの場合のみ読み込み）
# LOAD条件: 01b Phase1（前提条件判定）完了後に読み込み
# 遷移元: 01b_入院判定 S1B_END→★M02_CHK→M02完了後→ここ（S1F_1A）
# 復帰先: PHASE1_END→01b_入院判定 Phase2へ / PHASE1_END_SKIP→00_router:PHASE5_STARTへ
step_id	question	yes_next	no_next	yes_effect	no_effect
S1F_1A	同一事実について異なる内容の記載があるか？(入院期間・傷病発生日・事故日等の不一致)[AI]	S1F_1AY	S1F_1B	-	-
S1F_1AY	事実確認フラグON記録	S1F_1B	S1F_1B	FLAG_FACT_CHECK=ON	=
S1F_1B	傷病の原因・発生日が不明な箇所があるか？[AI]	S1F_1BY	S1F_1C_CHK	-	-
S1F_1BY	事実確認フラグON記録	S1F_1C_CHK	S1F_1C_CHK	FLAG_FACT_CHECK=ON	=
S1F_1C_CHK	前提条件OKの入院が1件以上あるか？	S1F_1C_PRE	S1F_FACT_STOP	-	-
S1F_1C_PRE	成人医療特約が付加されているか？(S1_2系で記録済み参照)	S1F_1C_A	S1F_1C_B	成人医療特約=付加あり	未付加
S1F_1C_A	【成人医療あり】入院開始後に治療が開始された、入院の主たる原因とは別の傷病があるか？(入院開始時点で既に治療目的に含まれている傷病は該当しない。判断基準は「発生日」ではなく「入院中に当該傷病の加療が開始されたか」)[AI]	S1F_1C_A_FLAG	S1F_FACT_STOP	入院中併発傷病=あり	-
S1F_1C_A_FLAG	入院中傷病の治療期間確認要+原因確認要フラグON記録	S1F_FACT_STOP	S1F_FACT_STOP	FLAG_入院中傷病治療期間確認=ON;FLAG_入院中傷病原因確認=ON	=
S1F_1C_B	【成人医療なし】入院開始後に治療が開始された、入院の主たる原因とは別の傷病があるか？(入院開始時点で既に治療目的に含まれている傷病は該当しない。判断基準は「発生日」ではなく「入院中に当該傷病の加療が開始されたか」)[AI]	S1F_1C_B_1	S1F_FACT_STOP	入院中併発傷病=あり	-
S1F_1C_B_1	疾病原因入院で入院中発生傷病も疾病か？(疾病+疾病併発)[AI]	S1F_FACT_STOP	S1F_1C_B_FLAG	-	-
S1F_1C_B_FLAG	入院中傷病の治療期間確認要フラグON記録	S1F_FACT_STOP	S1F_FACT_STOP	FLAG_入院中傷病治療期間確認=ON	=
S1F_FACT_STOP	確認要フラグ(FLAG_FACT_CHECK/FLAG_入院中傷病治療期間確認/FLAG_入院中傷病原因確認)のいずれかONか？	STOP_FACT	S1F_OK	確認要フラグ=あり	-
STOP_FACT	【STOP：査定者確認依頼】■FLAG_FACT_CHECK=ON:記載不一致/原因・発生日不明の解消 ■FLAG_入院中傷病治療期間確認=ON:入院中発生傷病の治療開始日・終了日 ■FLAG_入院中傷病原因確認=ON:入院中発生傷病の原因(原疾患影響/合併症/偶発的)	STOP_FACT_INPUT	-	処理中断	-
STOP_FACT_INPUT	【査定者入力待ち】確認結果を入力	STOP_FACT_CHK	-	確認結果=査定者入力値	入力待ち
STOP_FACT_CHK	査定者確認結果に「確認項目・確認方法・確定値(日付/原因等)」が入力データ上で明示されているか？[AI]	S1F_OK	STOP_FACT_HOLD	確認結果=根拠付きで確定	根拠不足
STOP_FACT_HOLD	【HOLD】確認結果の根拠不足。中断を維持し後続判定・算定を禁止	-	-	処理中断継続	=
S1F_OK	確認要否チェック完了記録。前提条件OK入院が1件以上あるか？	PHASE1_END	PHASE1_END_SKIP	-	-
PHASE1_END	Phase1完了。入院あり→01b_入院判定 Phase2へ遷移	→01b_入院判定_PHASE2	→01b_入院判定_PHASE2	フェーズ1=完了;入院処理=実行	=
PHASE1_END_SKIP	Phase1完了。入院なし→Phase5(手術判定)へ直接	→00_router:PHASE5_START	→00_router:PHASE5_START	フェーズ1=完了;入院給付金合計=0;入院処理=スキップ	=
