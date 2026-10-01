/* Validate an entire committed KMP1 owner set with q/8 bytes of scratch.
   Input is trusted, bounded graph metadata supplied by the Python DP decoder;
   all image bytes, coverage, progress and endpoints are independently checked. */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "KMP1 images require little-endian hosts"
#endif

#define MAGIC UINT64_C(0x314e574f504d484b)
typedef struct { uint64_t first; uint32_t width, copies; } block;
static void fail(const char *reason) { fprintf(stderr,"check_images: %s\n",reason); exit(2); }
static void read_exact(FILE *file,void *data,size_t bytes) {
    if(fread(data,1,bytes,file)!=bytes) fail("truncated image");
}

int main(int argc,char **argv) {
    uint32_t p,r,q,f,budget,n,workers,blocks;
    int64_t wanted_phase,wanted_done;
    char hash[65];
    if(scanf("%" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu32
             " %" SCNu32 " %" SCNu32 " %" SCNd64 " %" SCNd64 " %64s %" SCNu32,
             &p,&r,&q,&f,&budget,&n,&workers,&wanted_phase,&wanted_done,hash,&blocks)!=11)
        fail("invalid graph metadata");
    if(!q || !f || !budget || !n || !workers || workers>16 || argc!=(int)workers+1 ||
       !blocks || blocks>budget || r>31 || strlen(hash)!=64) fail("graph bounds");
    unsigned char digest[32];
    for(unsigned j=0;j<32;j++) {
        char digits[3]={hash[j*2],hash[j*2+1],0}; unsigned value;
        if(strspn(digits,"0123456789abcdef")!=2 || sscanf(digits,"%2x",&value)!=1) fail("digest hex");
        digest[j]=(unsigned char)value;
    }
    uint32_t polynomial[32];
    for(uint32_t j=0;j<=r;j++) if(scanf("%" SCNu32,&polynomial[j])!=1 || polynomial[j]>=p) fail("polynomial");
    struct rlimit limit;
    if(getrlimit(RLIMIT_AS,&limit)) fail("read memory limit");
    uint64_t maximum=((uint64_t)q+7)/8+(uint64_t)blocks*sizeof(block)+UINT64_C(67108864);
    if(limit.rlim_cur==RLIM_INFINITY || maximum<limit.rlim_cur) limit.rlim_cur=(rlim_t)maximum;
    if(setrlimit(RLIMIT_AS,&limit)) fail("set memory limit");
    block *graph=calloc(blocks,sizeof *graph);
    unsigned char *seen=calloc(((uint64_t)q+7)/8,1);
    if(!graph || !seen) fail("allocation");
    uint64_t first=0;
    for(uint32_t j=0;j<blocks;j++) {
        uint32_t stripes,copies;
        if(scanf("%" SCNu32 " %" SCNu32,&stripes,&copies)!=2 || !stripes || stripes>p || !copies ||
           (uint64_t)stripes*f>budget) fail("request block");
        graph[j]=(block){first,stripes*f,copies}; first+=(uint64_t)stripes*f*copies;
    }
    if(first!=n) fail("request count");
    uint64_t phase=0,claimed=0,matched=0;
    for(uint32_t rank=0;rank<workers;rank++) {
        FILE *file=fopen(argv[rank+1],"rb"); if(!file) fail("open image");
        uint64_t owned=0;
        for(uint32_t j=0;j<blocks;j++) if(graph[j].width>rank)
            owned+=(uint64_t)((graph[j].width-1-rank)/workers+1)*graph[j].copies;
        uint64_t header[10]; unsigned char actual_digest[32];
        read_exact(file,header,sizeof header); read_exact(file,actual_digest,sizeof actual_digest);
        if(header[0]!=MAGIC || header[1]!=p || header[2]!=r || header[3]!=q || header[4]!=n ||
           header[5]!=workers || header[6]!=rank || header[9]!=owned || memcmp(digest,actual_digest,32))
            fail("image identity or ownership mismatch");
        if(header[7]>n || header[8]>n) fail("image progress bounds");
        if(rank==0) { phase=header[7]; claimed=header[8]; }
        if(header[7]!=phase || header[8]!=claimed) fail("mixed image phases");
        for(uint32_t j=0;j<=r;j++) {
            uint32_t value; read_exact(file,&value,4); if(value!=polynomial[j]) fail("image polynomial mismatch");
        }
        for(uint32_t j=0;j<blocks;j++) for(uint32_t copy=0;copy<graph[j].copies;copy++)
            for(uint32_t cell=rank;cell<graph[j].width;cell+=workers) {
                uint32_t row[4]; read_exact(file,row,sizeof row);
                uint64_t expected=graph[j].first+(uint64_t)copy*graph[j].width+cell;
                if(row[0]!=expected || row[3]) fail("image coverage mismatch");
                if(row[1]==UINT32_MAX) {
                    if(row[2]!=UINT32_MAX) fail("unmatched image choice");
                    continue;
                }
                if(row[1]>=q || row[2]>=f) fail("image assignment bounds");
                unsigned char bit=(unsigned char)(1u<<(row[1]%8));
                if(seen[row[1]/8]&bit) fail("repeated image endpoint");
                seen[row[1]/8]|=bit; matched++;
            }
        if(fgetc(file)!=EOF || ferror(file) || fclose(file)) fail("image trailing bytes");
    }
    if(matched!=claimed || (wanted_phase>=0 && phase!=(uint64_t)wanted_phase) ||
       (wanted_done>=0 && matched!=(uint64_t)wanted_done)) fail("image committed count or cursor mismatch");
    printf("{\"phase\":%" PRIu64 ",\"done\":%" PRIu64 "}\n",phase,matched);
    free(graph); free(seen); return 0;
}
