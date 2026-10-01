#define _GNU_SOURCE
/* Owner-partitioned matching with optional durable, all-owner phase barriers. */
#include "kh_field.h"
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "This isolated prototype's wire and output format requires little-endian hosts"
#endif

#define NONE UINT32_MAX
#define MAX_PEERS 16
#define MAX_THREADS 64
typedef struct { uint32_t a,b,c,d; } message;
typedef struct { message *data; size_t n,cap; } vector;
typedef struct { uint32_t first,coset,copies,width; } block;
typedef struct { uint32_t id,mate,choice,root,distance,endpoint; } left_state;
typedef struct { uint32_t mate,predecessor,choice,traced; } right_state;
static kh_parameters_t par;
static uint16_t polynomial[32];
static unsigned rank_id, peers, threads, batch;
static int sockets[MAX_PEERS];
static block *blocks;
static unsigned block_count;
static uint32_t nleft, owned_cells, lo_right, hi_right;
static size_t owned_left;
static left_state *lefts;
static right_state *rights;
static uint32_t *cells, *positions, *frontier;
static _Atomic uint64_t *sent;
static message *generated;
static _Atomic size_t generated_count;
static uint64_t wire_bytes, candidate_count, scanned, phase_count, level_count;
static double exchange_seconds;
static volatile sig_atomic_t stopping;
static const char *checkpoint_path, *resume_path;
static unsigned checkpoint_seconds;
static unsigned checkpoint_phases;
static uint64_t checkpoint_phase_previous;
static unsigned char input_digest[32];
static bool managed;

static void request_stop(int signum) { (void)signum; stopping=1; }

static void die(const char *reason) {
    fprintf(stderr,"rank %u: %s (%s)\n",rank_id,reason,strerror(errno)); exit(2);
}
static void *allocate(size_t n,size_t size) {
    if (size && n>SIZE_MAX/size) die("allocation overflow");
    void *p=calloc(n?n:1,size); if(!p) die("allocation failed"); return p;
}
static double now(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9;
}
static uint32_t bound(uint32_t count,unsigned i) {
    return (uint32_t)(((uint64_t)count*i+peers-1)/peers);
}
static unsigned owner(uint32_t id,uint32_t count) {
    assert(id<count); return (unsigned)((uint64_t)id*peers/count);
}
static void push(vector *v,message m) {
    if(v->n==v->cap) {
        size_t cap=v->cap?v->cap*2:64;
        if(cap>SIZE_MAX/sizeof(message)) die("message capacity overflow");
        message *p=realloc(v->data,cap*sizeof *p); if(!p) die("message allocation");
        v->data=p; v->cap=cap;
    }
    v->data[v->n++]=m;
}
static void clear_vectors(vector *v) {
    for(unsigned i=0;i<peers;i++) { free(v[i].data); v[i]=(vector){0}; }
}
static void transfer(int fd,void *data,size_t count,bool writing) {
    unsigned char *p=data;
    while(count) {
        ssize_t n=writing?send(fd,p,count,MSG_NOSIGNAL):recv(fd,p,count,0);
        if(n<0 && errno==EINTR) continue;
        if(n<=0) die(writing?"peer write failed":"peer read failed");
        p+=n; count-=(size_t)n;
    }
}
typedef struct { unsigned peer; vector *out,*in; } exchange_task;
static void *exchange_peer(void *raw) {
    exchange_task *t=raw; int fd=sockets[t->peer]; uint64_t count=t->out->n,received;
    transfer(fd,&count,sizeof count,true); transfer(fd,&received,sizeof received,false);
    /* All messages are bounded scan/trace batches or constant-size reductions. */
    if(received>(uint64_t)batch*peers+1) die("oversized peer frame");
    t->in->data=allocate(received,sizeof(message)); t->in->n=received; t->in->cap=received;
    if(rank_id<t->peer) {
        transfer(fd,t->out->data,t->out->n*sizeof(message),true);
        transfer(fd,t->in->data,t->in->n*sizeof(message),false);
    } else {
        transfer(fd,t->in->data,t->in->n*sizeof(message),false);
        transfer(fd,t->out->data,t->out->n*sizeof(message),true);
    }
    return NULL;
}
static void exchange(vector *out,vector *in) {
    double start=now(); pthread_t jobs[MAX_PEERS]; exchange_task tasks[MAX_PEERS];
    for(unsigned i=0;i<peers;i++) if(i!=rank_id) {
        tasks[i]=(exchange_task){i,&out[i],&in[i]};
        if(pthread_create(&jobs[i],NULL,exchange_peer,&tasks[i])) die("peer thread creation");
    }
    in[rank_id]=out[rank_id]; out[rank_id]=(vector){0};
    for(unsigned i=0;i<peers;i++) if(i!=rank_id) {
        pthread_join(jobs[i],NULL); wire_bytes+=sizeof(uint64_t)+out[i].n*sizeof(message);
    }
    clear_vectors(out); exchange_seconds+=now()-start;
}
static uint64_t sum(uint64_t value) {
    vector out[MAX_PEERS]={0},in[MAX_PEERS]={0};
    for(unsigned i=0;i<peers;i++) push(&out[i],(message){(uint32_t)value,(uint32_t)(value>>32),0,0});
    exchange(out,in); uint64_t result=0;
    for(unsigned i=0;i<peers;i++) { assert(in[i].n==1); result+=in[i].data[0].a+((uint64_t)in[i].data[0].b<<32); }
    clear_vectors(in); return result;
}

/* A persistent pinned compute pool; peer I/O threads are deliberately simpler in this PoC. */
static pthread_mutex_t pool_mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pool_condition=PTHREAD_COND_INITIALIZER;
static uint64_t pool_generation;
static unsigned pool_finished;
static bool pool_stop;
static void (*pool_function)(unsigned);
static pthread_t pool_threads[MAX_THREADS];
static unsigned pool_ids[MAX_THREADS];
static int cpu_ids[MAX_THREADS];
static void *compute_main(void *raw) {
    unsigned id=*(unsigned *)raw; cpu_set_t mask; CPU_ZERO(&mask); CPU_SET(cpu_ids[id],&mask);
    if(pthread_setaffinity_np(pthread_self(),sizeof mask,&mask)) die("compute affinity");
    uint64_t generation=0; pthread_mutex_lock(&pool_mutex);
    while(!pool_stop) {
        while(!pool_stop && generation==pool_generation) pthread_cond_wait(&pool_condition,&pool_mutex);
        if(pool_stop) break;
        generation=pool_generation; void (*fn)(unsigned)=pool_function;
        pthread_mutex_unlock(&pool_mutex); fn(id); pthread_mutex_lock(&pool_mutex);
        pool_finished++; pthread_cond_broadcast(&pool_condition);
    }
    pthread_mutex_unlock(&pool_mutex); return NULL;
}
static void run_pool(void (*fn)(unsigned)) {
    pthread_mutex_lock(&pool_mutex); pool_finished=0; pool_function=fn; pool_generation++;
    pthread_cond_broadcast(&pool_condition);
    while(pool_finished!=threads) pthread_cond_wait(&pool_condition,&pool_mutex);
    pthread_mutex_unlock(&pool_mutex);
}
static void initialize_pool(void) {
    cpu_set_t mask; if(sched_getaffinity(0,sizeof mask,&mask)) die("read affinity");
    unsigned count=0;
    for(int i=0;i<CPU_SETSIZE && count<threads;i++) if(CPU_ISSET(i,&mask)) cpu_ids[count++]=i;
    if(count<threads) die("too few granted CPUs");
    for(unsigned i=0;i<threads;i++) { pool_ids[i]=i; if(pthread_create(&pool_threads[i],NULL,compute_main,&pool_ids[i])) die("compute thread"); }
}
static void stop_pool(void) {
    pthread_mutex_lock(&pool_mutex); pool_stop=true; pthread_cond_broadcast(&pool_condition); pthread_mutex_unlock(&pool_mutex);
    for(unsigned i=0;i<threads;i++) pthread_join(pool_threads[i],NULL);
}

static void request(uint32_t id,uint32_t *coset,uint32_t *cell) {
    unsigned low=0,high=block_count;
    while(low+1<high) { unsigned mid=(low+high)/2; if(blocks[mid].first<=id) low=mid; else high=mid; }
    const block *b=&blocks[low]; uint32_t offset=id-b->first;
    *coset=b->coset+offset/b->width; *cell=offset%b->width;
}
static unsigned left_owner(uint32_t id) {
    uint32_t coset,cell; request(id,&coset,&cell); return cell%peers;
}
static left_state *local_left(uint32_t id) {
    size_t low=0,high=owned_left;
    while(low<high) { size_t mid=(low+high)/2; if(lefts[mid].id<id) low=mid+1; else high=mid; }
    if(low>=owned_left || lefts[low].id!=id) die("left routed to wrong owner");
    return &lefts[low];
}
static uint32_t times_x(uint32_t element) {
    uint32_t top=element/(par.q/par.p), rest=(element%(par.q/par.p))*par.p;
    if(par.p==2) {
        uint32_t packed=0; for(unsigned j=0;j<par.r;j++) packed|=(uint32_t)polynomial[j]<<j;
        return top?rest^packed:rest;
    }
    uint32_t result=0,place=1;
    for(unsigned j=0;j<par.r;j++) {
        result+=((rest%par.p+par.p-top*polynomial[j]%par.p)%par.p)*place;
        rest/=par.p; place*=par.p;
    }
    return result;
}
static uint32_t multiply(uint32_t a,uint32_t b) {
    uint32_t result=0;
    for(unsigned j=0;j<par.r;j++) {
        uint32_t digit=b%par.p, x=a, y=result, place=1, next=0;
        for(unsigned k=0;k<par.r;k++) {
            next+=((y%par.p+digit*(x%par.p))%par.p)*place;
            y/=par.p; x/=par.p; place*=par.p;
        }
        result=next; b/=par.p; a=times_x(a);
    }
    return result;
}
static uint32_t power_x(uint32_t exponent) {
    uint32_t result=1,base=par.p;
    while(exponent) { if(exponent&1) result=multiply(result,base); exponent>>=1; if(exponent) base=multiply(base,base); }
    return result;
}
static uint32_t cell_index(uint32_t element) {
    uint32_t suffix=element%par.f,leading=element/par.f,prefix=0;
    while(leading) { prefix=(prefix+leading%par.p)%par.p; leading/=par.p; }
    return prefix*par.f+suffix;
}
static uint64_t work_begin,work_count;
static void generate_field(unsigned thread) {
    uint64_t begin=work_begin+work_count*thread/threads,end=work_begin+work_count*(thread+1)/threads;
    uint32_t element=begin?power_x((uint32_t)begin-1):0;
    for(uint64_t label=begin;label<end;label++) {
        if(label==1) element=1;
        generated[label-work_begin]=(message){cell_index(element),(uint32_t)label,0,0};
        if(label) element=times_x(element);
    }
}
static int compare_u32(const void *a,const void *b) {
    uint32_t x=*(const uint32_t *)a,y=*(const uint32_t *)b; return (x>y)-(x<y);
}
static void sort_cells(unsigned thread) {
    uint32_t n=owned_cells,begin=n*thread/threads,end=n*(thread+1)/threads;
    for(uint32_t i=begin;i<end;i++) {
        if(positions[i]!=par.f) die("incorrect cell population");
        qsort(cells+(uint64_t)i*par.f,par.f,sizeof(uint32_t),compare_u32);
    }
}
static void build_field(void) {
    for(uint64_t start=0;start<par.q;start+=(uint64_t)batch*peers) {
        work_begin=start+(uint64_t)rank_id*batch;
        work_count=work_begin<par.q?par.q-work_begin:0; if(work_count>batch) work_count=batch;
        run_pool(generate_field); vector out[MAX_PEERS]={0},in[MAX_PEERS]={0};
        for(size_t i=0;i<work_count;i++) push(&out[generated[i].a%peers],generated[i]);
        exchange(out,in);
        for(unsigned peer=0;peer<peers;peer++) for(size_t j=0;j<in[peer].n;j++) {
            message m=in[peer].data[j]; if(m.a%peers!=rank_id || m.a>=par.budget) die("field ownership");
            uint32_t cell=m.a/peers,offset=positions[cell]++;
            if(offset>=par.f) die("cell overflow");
            cells[(uint64_t)cell*par.f+offset]=m.b;
        }
        clear_vectors(in);
    }
    run_pool(sort_cells);
}

static void scan_frontier(unsigned thread) {
    uint64_t begin=work_begin+work_count*thread/threads,end=work_begin+work_count*(thread+1)/threads;
    uint32_t cached=NONE,coset=0,cell=0;
    for(uint64_t edge=begin;edge<end;edge++) {
        left_state *left=&lefts[frontier[edge/par.f]];
        if(left->id!=cached) { request(left->id,&coset,&cell); cached=left->id; }
        uint32_t k=(uint32_t)(edge%par.f),z=cells[(uint64_t)(cell/peers)*par.f+k];
        uint32_t right=z?1+(uint32_t)(((uint64_t)z-1+par.q-1-coset)%(par.q-1)):0;
        if(right==left->mate) continue;
        uint64_t bit=UINT64_C(1)<<(right%64);
        if(atomic_fetch_or_explicit(&sent[right/64],bit,memory_order_relaxed)&bit) continue;
        size_t slot=atomic_fetch_add_explicit(&generated_count,1,memory_order_relaxed);
        assert(slot<batch); generated[slot]=(message){right,left->id,k,left->root};
    }
}
static bool search(void) {
    memset(sent,0,((uint64_t)par.q+63)/64*sizeof *sent);
    for(size_t i=0;i<owned_left;i++) {
        left_state *u=&lefts[i]; u->endpoint=NONE;
        u->distance=u->mate==NONE?0:NONE; u->root=u->mate==NONE?u->id:NONE;
    }
    for(uint32_t i=0;i<hi_right-lo_right;i++) { rights[i].predecessor=NONE; rights[i].traced=0; }
    for(uint32_t level=0;;level++) {
        level_count++; size_t count=0;
        for(size_t i=0;i<owned_left;i++) if(lefts[i].distance==level) frontier[count++]=(uint32_t)i;
        if(!sum(count)) return false;
        uint64_t total=(uint64_t)count*par.f,cursor=0,found=0;
        for(;;) {
            /* Search does not mutate the matching. A collective stop can leave
               a long BFS at a batch boundary and checkpoint its last committed
               matching, rather than waiting for an enormous full edge scan. */
            uint64_t work=sum((cursor<total?1:0)+(managed && stopping?peers+1:0));
            if(work>peers) return false;
            if(!work) break;
            work_begin=cursor; work_count=total-cursor; if(work_count>batch) work_count=batch;
            generated_count=0; run_pool(scan_frontier); scanned+=work_count; cursor+=work_count;
            vector out[MAX_PEERS]={0},in[MAX_PEERS]={0};
            candidate_count+=generated_count;
            for(size_t i=0;i<generated_count;i++) push(&out[owner(generated[i].a,par.q)],generated[i]);
            exchange(out,in);
            for(unsigned peer=0;peer<peers;peer++) for(size_t j=0;j<in[peer].n;j++) {
                message m=in[peer].data[j]; if(m.a<lo_right || m.a>=hi_right) die("right ownership");
                right_state *v=&rights[m.a-lo_right]; if(v->predecessor!=NONE) continue;
                v->predecessor=m.b; v->choice=m.c;
                if(v->mate==NONE) { push(&out[left_owner(m.d)],(message){m.d,m.a,0,1}); found++; }
                else push(&out[left_owner(v->mate)],(message){v->mate,m.d,level+1,0});
            }
            clear_vectors(in); exchange(out,in);
            for(unsigned peer=0;peer<peers;peer++) for(size_t j=0;j<in[peer].n;j++) {
                message m=in[peer].data[j]; left_state *u=local_left(m.a);
                if(m.d) { assert(u->mate==NONE); if(m.b<u->endpoint) u->endpoint=m.b; }
                else if(u->distance==NONE) { u->distance=m.c; u->root=m.b; }
            }
            clear_vectors(in);
        }
        if(sum(found)) return true;
    }
}
static uint64_t augment(void) {
    /* Disjoint forest paths can be installed in place between search barriers.
       Any process failure aborts this non-recoverable prototype; no output is published. */
    size_t next_root=0; uint64_t added=0;
    while(sum(next_root<owned_left)) {
        vector out[MAX_PEERS]={0},in[MAX_PEERS]={0}; unsigned roots=0;
        while(next_root<owned_left && roots<batch) {
            left_state *u=&lefts[next_root++];
            if(u->mate==NONE && u->endpoint!=NONE) {
                push(&out[owner(u->endpoint,par.q)],(message){u->endpoint,u->id,0,0}); roots++;
            }
        }
        uint64_t pending=roots;
        while(sum(pending)) {
            exchange(out,in);
            for(unsigned peer=0;peer<peers;peer++) for(size_t j=0;j<in[peer].n;j++) {
                message m=in[peer].data[j]; right_state *v=&rights[m.a-lo_right];
                if(v->predecessor==NONE || v->traced) die("invalid or intersecting path");
                v->traced=1; v->mate=v->predecessor;
                push(&out[left_owner(v->predecessor)],(message){v->predecessor,m.a,v->choice,m.b});
            }
            clear_vectors(in); exchange(out,in); pending=0;
            for(unsigned peer=0;peer<peers;peer++) for(size_t j=0;j<in[peer].n;j++) {
                message m=in[peer].data[j]; left_state *u=local_left(m.a);
                if(u->root!=m.d) die("path root mismatch");
                uint32_t old=u->mate; u->mate=m.b; u->choice=m.c;
                if(old==NONE) { if(u->id!=m.d) die("wrong path endpoint"); added++; }
                else { push(&out[owner(old,par.q)],(message){old,m.d,0,0}); pending++; }
            }
            clear_vectors(in);
        }
        clear_vectors(out);
    }
    return sum(added);
}

static void connect_peers(char *hosts,unsigned port,const char *token) {
    char *addresses[MAX_PEERS],*save=NULL; unsigned n=0;
    for(char *s=strtok_r(hosts,",",&save);s;s=strtok_r(NULL,",",&save)) {
        if(n>=peers) die("too many addresses");
        addresses[n++]=s;
    }
    if(n!=peers) die("wrong address count");
    int listener=socket(AF_INET,SOCK_STREAM,0); if(listener<0) die("listen socket");
    int yes=1; setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof yes);
    struct sockaddr_in bind_address={.sin_family=AF_INET,.sin_port=htons((uint16_t)(port+rank_id)),.sin_addr.s_addr=INADDR_ANY};
    if(bind(listener,(void *)&bind_address,sizeof bind_address) || listen(listener,MAX_PEERS)) die("bind/listen");
    for(unsigned i=0;i<rank_id;i++) {
        struct sockaddr_in address={.sin_family=AF_INET,.sin_port=htons((uint16_t)(port+i))};
        if(inet_pton(AF_INET,addresses[i],&address.sin_addr)!=1) die("invalid peer IP");
        double deadline=now()+30; int fd;
        for(;;) {
            fd=socket(AF_INET,SOCK_STREAM,0);
            if(connect(fd,(void *)&address,sizeof address)==0) break;
            close(fd); if(now()>deadline) die("peer connect timeout"); usleep(10000);
        }
        sockets[i]=fd; uint32_t id=rank_id;
        transfer(fd,&id,4,true); transfer(fd,(void *)token,32,true);
    }
    for(unsigned i=rank_id+1;i<peers;i++) {
        int fd=accept(listener,NULL,NULL); if(fd<0) die("accept");
        uint32_t id; char received[32]; transfer(fd,&id,4,false); transfer(fd,received,32,false);
        if(id<=rank_id || id>=peers || sockets[id]!=-1 || memcmp(received,token,32)) die("invalid peer handshake");
        sockets[id]=fd;
    }
    close(listener);
    for(unsigned i=0;i<peers;i++) if(i!=rank_id) {
        setsockopt(sockets[i],IPPROTO_TCP,TCP_NODELAY,&yes,sizeof yes);
        struct timeval timeout={.tv_sec=120};
        setsockopt(sockets[i],SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof timeout);
        setsockopt(sockets[i],SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof timeout);
    }
}
static void load(const char *path) {
    FILE *f=fopen(path,"r"); if(!f) die("open graph input"); unsigned p,r; const char *error=NULL;
    if(fscanf(f,"%u%u%u%u",&p,&r,&nleft,&block_count)!=4 || !kh_parameters(p,r,&par,&error)) die("graph dimensions");
    if(!block_count || block_count>par.budget || !nleft) die("graph bounds");
    for(unsigned i=0;i<=r;i++) { unsigned x; if(fscanf(f,"%u",&x)!=1 || x>=p) die("polynomial"); polynomial[i]=(uint16_t)x; }
    if(!kh_primitive(&par,polynomial)) die("nonprimitive polynomial");
    blocks=allocate(block_count,sizeof *blocks); uint64_t first=0,coset=0;
    for(unsigned i=0;i<block_count;i++) {
        unsigned stripes,copies;
        if(fscanf(f,"%u%u",&stripes,&copies)!=2 || !stripes || stripes>p || !copies) die("request block");
        uint64_t width=(uint64_t)stripes*par.f;
        if(first>UINT32_MAX || coset>=par.q-1) die("block overflow");
        blocks[i]=(block){(uint32_t)first,(uint32_t)coset,copies,(uint32_t)width};
        first+=width*copies; coset+=copies;
    }
    fclose(f); if(first!=nleft || coset+1>par.q-1) die("inconsistent request count");
    owned_cells=rank_id<par.budget?(par.budget-1-rank_id)/peers+1:0;
    lo_right=bound(par.q,rank_id); hi_right=bound(par.q,rank_id+1);
    for(unsigned i=0;i<block_count;i++) {
        uint32_t width=blocks[i].width;
        if(width>rank_id) owned_left+=(size_t)((width-1-rank_id)/peers+1)*blocks[i].copies;
    }
    lefts=allocate(owned_left,sizeof *lefts); frontier=allocate(owned_left,sizeof *frontier);
    size_t at=0;
    for(unsigned i=0;i<block_count;i++) {
        uint32_t end=blocks[i].width;
        for(uint32_t copy=0;copy<blocks[i].copies;copy++) for(uint32_t cell=rank_id;cell<end;cell+=peers) {
            lefts[at++]=(left_state){.id=blocks[i].first+copy*blocks[i].width+cell,.mate=NONE,.choice=NONE};
        }
    }
    assert(at==owned_left);
    rights=allocate(hi_right-lo_right,sizeof *rights);
    for(uint32_t i=0;i<hi_right-lo_right;i++) rights[i].mate=NONE;
    cells=allocate((size_t)owned_cells*par.f,sizeof *cells);
    positions=allocate(owned_cells,sizeof *positions);
    sent=allocate(((uint64_t)par.q+63)/64,sizeof *sent); generated=allocate(batch,sizeof *generated);
}

/* A phase image contains only canonical owned left assignments. Field labels,
   BFS scratch and right mates are rebuilt and checked when restoring. Files
   are captured only while every owner is at the same acknowledged barrier. */
#define IMAGE_MAGIC UINT64_C(0x314e574f504d484b)
static void image_header(uint64_t *header,uint64_t matched) {
    uint64_t values[10]={IMAGE_MAGIC,par.p,par.r,par.q,nleft,peers,rank_id,phase_count,matched,owned_left};
    memcpy(header,values,sizeof values);
}
static void save_image(uint64_t matched) {
    size_t length=strlen(checkpoint_path)+5;
    char *temporary=allocate(length,1); snprintf(temporary,length,"%s.tmp",checkpoint_path);
    FILE *file=fopen(temporary,"wb"); if(!file) die("create phase image");
    uint64_t header[10]; image_header(header,matched);
    if(fwrite(header,sizeof header,1,file)!=1 || fwrite(input_digest,32,1,file)!=1) die("image header");
    for(unsigned j=0;j<=par.r;j++) { uint32_t value=polynomial[j]; if(fwrite(&value,4,1,file)!=1) die("image polynomial"); }
    for(size_t i=0;i<owned_left;i++) {
        uint32_t row[4]={lefts[i].id,lefts[i].mate,lefts[i].choice,0};
        if(fwrite(row,sizeof row,1,file)!=1) die("image record");
    }
    if(fflush(file) || fsync(fileno(file)) || fclose(file) || rename(temporary,checkpoint_path)) die("commit image");
    free(temporary);
}
static uint64_t restore_image(void) {
    if(!resume_path) return 0;
    FILE *file=fopen(resume_path,"rb"); if(!file) die("open phase image");
    uint64_t header[10],expected[10]; unsigned char digest[32];
    if(fread(header,sizeof header,1,file)!=1 || fread(digest,32,1,file)!=1) die("truncated image");
    image_header(expected,0);
    for(unsigned j=0;j<10;j++) if(j!=7 && j!=8 && header[j]!=expected[j]) die("image ownership or graph mismatch");
    if(memcmp(digest,input_digest,32) || header[8]>nleft) die("image identity mismatch");
    for(unsigned j=0;j<=par.r;j++) { uint32_t value; if(fread(&value,4,1,file)!=1 || value!=polynomial[j]) die("image polynomial mismatch"); }
    uint64_t local_matched=0;
    for(size_t i=0;i<owned_left;i++) {
        uint32_t row[4]; if(fread(row,sizeof row,1,file)!=1 || row[0]!=lefts[i].id || row[3]) die("image request order");
        if(row[1]==NONE) { if(row[2]!=NONE) die("unmatched image choice"); }
        else {
            if(row[1]>=par.q || row[2]>=par.f) die("image assignment bounds");
            uint32_t coset,cell; request(row[0],&coset,&cell);
            uint32_t z=cells[(uint64_t)(cell/peers)*par.f+row[2]];
            uint32_t v=z?1+(uint32_t)(((uint64_t)z-1+par.q-1-coset)%(par.q-1)):0;
            if(v!=row[1]) die("image assignment is not an edge");
            local_matched++;
        }
        lefts[i].mate=row[1]; lefts[i].choice=row[2];
    }
    if(fgetc(file)!=EOF || ferror(file) || fclose(file)) die("image trailing bytes");
    /* All participants must have restored exactly the same committed phase. */
    vector out[MAX_PEERS]={0},in[MAX_PEERS]={0};
    for(unsigned i=0;i<peers;i++) push(&out[i],(message){(uint32_t)header[7],(uint32_t)(header[7]>>32),(uint32_t)header[8],0});
    exchange(out,in);
    for(unsigned i=0;i<peers;i++) {
        if(in[i].n!=1 || in[i].data[0].a!=(uint32_t)header[7] ||
           in[i].data[0].b!=(uint32_t)(header[7]>>32) || in[i].data[0].c!=header[8]) die("mixed checkpoint phases");
    }
    clear_vectors(in);
    size_t offset=0;
    while(sum(offset<owned_left)) {
        size_t end=offset+batch; if(end>owned_left) end=owned_left;
        for(;offset<end;offset++) if(lefts[offset].mate!=NONE)
            push(&out[owner(lefts[offset].mate,par.q)],(message){lefts[offset].mate,lefts[offset].id,0,0});
        exchange(out,in);
        for(unsigned i=0;i<peers;i++) for(size_t j=0;j<in[i].n;j++) {
            message m=in[i].data[j];
            if(m.a<lo_right || m.a>=hi_right || rights[m.a-lo_right].mate!=NONE) die("repeated image right endpoint");
            rights[m.a-lo_right].mate=m.b;
        }
        clear_vectors(in);
    }
    if(sum(local_matched)!=header[8]) die("image cardinality mismatch");
    phase_count=header[7]; return header[8];
}
static bool checkpoint_barrier(uint64_t matched,double *last) {
    if(!managed) return false;
    uint64_t stop=sum(stopping!=0);
    bool timed=checkpoint_seconds && now()-*last>=checkpoint_seconds;
    bool phased=checkpoint_phases && phase_count>checkpoint_phase_previous && phase_count%checkpoint_phases==0;
    uint64_t due=sum(rank_id==0 && (timed || phased));
    if(stop || due) {
        save_image(matched); sum(0);
        printf("{\"event\":\"checkpoint\",\"cursor\":%" PRIu64 ",\"done\":%" PRIu64 ",\"total\":%u}\n",phase_count,matched,nleft);
        fflush(stdout);
        if(getchar()!='\n') die("checkpoint acknowledgement missing");
        sum(0); *last=now(); checkpoint_phase_previous=phase_count;
    }
    return stop!=0;
}
int main(int argc,char **argv) {
    if(argc<10) { fprintf(stderr,"usage: kh_match_multi GRAPH RANK PEERS HOSTS PORT THREADS BATCH TOKEN OUTPUT [--checkpoint PATH --checkpoint-seconds N --identity HASH --max-bytes N --resume PATH]\n"); return 2; }
    uint64_t max_bytes=0;
    bool have_identity=false;
    for(int i=10;i<argc;i++) {
        if(i+1>=argc) die("missing managed option value");
        const char *option=argv[i++],*value=argv[i];
        if(!strcmp(option,"--checkpoint")) { checkpoint_path=value; managed=true; }
        else if(!strcmp(option,"--resume")) resume_path=value;
        else if(!strcmp(option,"--checkpoint-seconds")) checkpoint_seconds=(unsigned)strtoul(value,NULL,10);
        else if(!strcmp(option,"--checkpoint-phases")) checkpoint_phases=(unsigned)strtoul(value,NULL,10);
        else if(!strcmp(option,"--max-bytes")) max_bytes=strtoull(value,NULL,10);
        else if(!strcmp(option,"--identity")) {
            have_identity=true;
            if(strlen(value)!=64) die("input identity length");
            for(unsigned j=0;j<32;j++) {
                unsigned byte; char digits[3]={value[j*2],value[j*2+1],0};
                if(sscanf(digits,"%2x",&byte)!=1 || strspn(digits,"0123456789abcdef")!=2) die("input identity hex");
                input_digest[j]=(unsigned char)byte;
            }
        } else die("unknown managed option");
    }
    if(managed) {
        if(!max_bytes || !have_identity) die("managed owner requires memory limit and input identity");
        struct rlimit limit; if(getrlimit(RLIMIT_AS,&limit)) die("read memory limit");
        if(limit.rlim_cur==RLIM_INFINITY || max_bytes<limit.rlim_cur) limit.rlim_cur=(rlim_t)max_bytes;
        if(setrlimit(RLIMIT_AS,&limit)) die("set memory limit");
        struct sigaction action={0}; action.sa_handler=request_stop; sigemptyset(&action.sa_mask);
        if(sigaction(SIGTERM,&action,NULL) || sigaction(SIGINT,&action,NULL)) die("stop handler");
    } else if(argc!=10) die("managed options require checkpoint path");
    rank_id=(unsigned)strtoul(argv[2],NULL,10); peers=(unsigned)strtoul(argv[3],NULL,10);
    unsigned port=(unsigned)strtoul(argv[5],NULL,10); threads=(unsigned)strtoul(argv[6],NULL,10);
    batch=(unsigned)strtoul(argv[7],NULL,10);
    if(!peers || peers>MAX_PEERS || rank_id>=peers || !threads || threads>MAX_THREADS ||
       batch<1 || batch>1048576 || port<1024 || port+peers>65535 || strlen(argv[8])!=32) die("invalid arguments");
    for(unsigned i=0;i<MAX_PEERS;i++) sockets[i]=-1;
    double start=now(); load(argv[1]); initialize_pool(); connect_peers(argv[4],port,argv[8]);
    double field_start=now(); build_field(); sum(0); double field_seconds=now()-field_start;
    uint64_t matched=restore_image(); double search_start=now(),last_checkpoint=now();
    checkpoint_phase_previous=phase_count;
    if(checkpoint_barrier(matched,&last_checkpoint)) return 75;
    while(matched<nleft && search()) {
        uint64_t added=augment(); if(!added) die("reachable endpoint without augmentation");
        matched+=added; phase_count++;
        if(rank_id==0) fprintf(stderr,"phase=%" PRIu64 " matched=%" PRIu64 "/%u\n",phase_count,matched,nleft);
        if(managed) { printf("{\"event\":\"progress\",\"done\":%" PRIu64 ",\"total\":%u,\"cursor\":%" PRIu64 "}\n",matched,nleft,phase_count); fflush(stdout); }
        if(checkpoint_barrier(matched,&last_checkpoint)) return 75;
    }
    double search_seconds=now()-search_start;
    if(managed && checkpoint_barrier(matched,&last_checkpoint)) return 75;
    uint64_t actual=0; for(size_t i=0;i<owned_left;i++) actual+=lefts[i].mate!=NONE;
    if(sum(actual)!=matched) die("cardinality accounting");
    FILE *output=fopen(argv[9],"wbx"); if(!output) die("create output (must be new)");
    for(size_t i=0;i<owned_left;i++) {
        left_state *u=&lefts[i]; uint32_t record[4]={u->id,u->mate,u->choice,u->distance!=NONE};
        if(fwrite(record,sizeof record,1,output)!=1) die("write output");
    }
    if(fclose(output)) die("close output");
    struct rusage usage; getrusage(RUSAGE_SELF,&usage);
    uint64_t owned_bytes=owned_left*(sizeof(left_state)+sizeof(uint32_t))+
        (uint64_t)(hi_right-lo_right)*sizeof(right_state)+(uint64_t)owned_cells*par.f*4+
        (uint64_t)owned_cells*4+((uint64_t)par.q+63)/64*sizeof(*sent);
    printf("{\"rank\":%u,\"p\":%u,\"r\":%u,\"required\":%u,\"matched\":%" PRIu64
           ",\"owned_left\":%zu,\"owned_right\":%u,\"owned_field_labels\":%" PRIu64
           ",\"owned_bytes\":%" PRIu64 ",\"peak_rss_bytes\":%ld,\"field_seconds\":%.6f,\"search_seconds\":%.6f"
           ",\"elapsed_seconds\":%.6f,\"exchange_seconds\":%.6f,\"phases\":%" PRIu64
           ",\"levels\":%" PRIu64 ",\"scanned_edges\":%" PRIu64 ",\"candidates\":%" PRIu64
           ",\"sent_bytes\":%" PRIu64 "}\n",
           rank_id,par.p,par.r,nleft,matched,owned_left,hi_right-lo_right,(uint64_t)owned_cells*par.f,
           owned_bytes,usage.ru_maxrss*1024,field_seconds,search_seconds,now()-start,exchange_seconds,
           phase_count,level_count,scanned,candidate_count,wire_bytes);
    stop_pool(); for(unsigned i=0;i<peers;i++) if(i!=rank_id) close(sockets[i]);
    free(lefts); free(rights); free(cells); free(positions); free(frontier); free(sent); free(generated); free(blocks);
    return 0;
}
