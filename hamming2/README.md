
make -C hamming2 odd_pattern

./odd_peek --pattern 2^5
./odd_pattern --pattern pattern_2_5.json -o M_33_32_192.pa.txt --verify

./odd_c --pattern pattern_2_5.json -o out.pa.txt
