<!-- nohup python3 search_f_exact_long.py \
  --hours 11.5 \
  --max-k 0 \
  --save-minutes 10 \
  --output-dir f_search_with_lineage \
  > f_search_with_lineage.log 2>&1 &
   -->


python3 search_f_exact_long.py \
  --jobs 14 \
  --parallel-from 100 \
  --hours 11.5 \
  --max-k 0 \
  --save-minutes 10 \
  --output-dir f_search_parallel


python3 search_f_exact_long.py \
  --jobs 14 \
  --parallel-from 100 \
  --hours 0.5 \
  --max-k 0 \
  --save-minutes 1 \
  --output-dir f_search_parallel
