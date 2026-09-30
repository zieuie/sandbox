
results=~/Documents/good-friday/results.md
while true; do
    python3 cluster/continuous_campaign.py --state cluster/deployments/continuous-campaign status 2>&1 | tee "$results"
    python3 cluster/kh.py --leader http://192.168.4.151:8061 status 2>&1 | tee -a "$results"
    sleep 600
done
