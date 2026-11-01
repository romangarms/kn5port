// XMem LZX decoder (frame-prefixed LZX stream, Xbox 360 XMemCompress "native" framing).
#include <stdint.h>
#include <stddef.h>
void*memset(void*d,int c,size_t n){volatile uint8_t*p=d;while(n--)*p++=c;return d;}
void*memcpy(void*d,const void*s,size_t n){volatile uint8_t*p=d;const uint8_t*q=s;while(n--)*p++=*q++;return d;}
int _fltused=0;
#define MAINSZ 656
#define LENSZ 249
typedef struct{const uint8_t*p,*e;uint32_t buf;int n;}BR;
static inline void fill(BR*b){while(b->n<=16){uint32_t w=0;if(b->p+1<b->e){w=b->p[0]|(b->p[1]<<8);}b->p+=2;b->buf|=w<<(16-b->n);b->n+=16;}}
static inline uint32_t peek(BR*b,int k){fill(b);return k?b->buf>>(32-k):0;}
static inline void drop(BR*b,int k){b->buf<<=k;b->n-=k;}
static inline uint32_t rd(BR*b,int k){if(k>16){uint32_t h=rd(b,k-16);return (h<<16)|rd(b,16);}uint32_t v=peek(b,k);drop(b,k);return v;}
typedef struct{uint16_t first[18];uint16_t cnt[18];uint16_t idx[18];uint16_t sym[MAINSZ];}Tree;
static void build(Tree*t,const uint8_t*l,int n){
  int c[17]={0};for(int i=0;i<n;i++)c[l[i]]++;c[0]=0;
  int code=0,idx=0;for(int b=1;b<=16;b++){code<<=1;t->first[b]=code;t->cnt[b]=c[b];t->idx[b]=idx;code+=c[b];idx+=c[b];}
  int pos[17];int a=0;for(int b=1;b<=16;b++){pos[b]=a;a+=c[b];}
  for(int i=0;i<n;i++)if(l[i])t->sym[pos[l[i]]++]=i;
}
static inline int dec(BR*b,Tree*t){uint32_t v=peek(b,16);
  for(int k=1;k<=16;k++){uint32_t code=v>>(16-k);uint32_t off=code-t->first[k];if(code>=t->first[k]&&off<t->cnt[k]){drop(b,k);return t->sym[t->idx[k]+off];}}
  return -1;}
static int rlens(BR*b,uint8_t*len,int s,int e){uint8_t pl[20];Tree pt;for(int i=0;i<20;i++)pl[i]=rd(b,4);build(&pt,pl,20);
  for(int x=s;x<e;){int z=dec(b,&pt);if(z<0)return -1;
    if(z==17){int y=rd(b,4)+4;while(y--&&x<e)len[x++]=0;}
    else if(z==18){int y=rd(b,5)+20;while(y--&&x<e)len[x++]=0;}
    else if(z==19){int y=rd(b,1)+4;z=dec(b,&pt);if(z<0)return -1;z=(len[x]+17-z)%17;while(y--&&x<e)len[x++]=z;}
    else{z=(len[x]+17-z)%17;len[x++]=z;}}
  return 0;}
__declspec(dllexport) int lzx_decode(const uint8_t*in,int clen,uint8_t*out,int ulen,int wbits,uint8_t*work){
  static const int slotsT[]={30,32,34,36,38,42,50};int slots=slotsT[wbits-15];
  uint8_t ex[51];uint32_t base[51];for(int i=0;i<51;i++){int e=i<4?0:(i-2)/2;if(e>17)e=17;ex[i]=e;}base[0]=0;for(int i=0;i<50;i++)base[i+1]=base[i]+(1u<<ex[i]);
  uint32_t wsz=1u<<wbits,wmask=wsz-1;uint8_t*w=work;memset(w,0,wsz);uint32_t wp=0;
  uint8_t ml[MAINSZ],ll[LENSZ],al[8];memset(ml,0,sizeof ml);memset(ll,0,sizeof ll);
  Tree*mtp=(Tree*)(work+wsz),*ltp=mtp+1,*atp=mtp+2;
#define mt (*mtp)
#define lt (*ltp)
#define at (*atp)

  uint32_t R0=1,R1=1,R2=1;int btype=0;uint32_t brem=0,blen=0;int hdr=0;int op=0,ip=0,rc=0;int lenempty=0;
  while(op<ulen){
    int fu=32768;if(ip>=clen){rc=-2;break;}
    if(in[ip]==0xFF&&ip+3<clen){fu=(in[ip+1]<<8)|in[ip+2];ip+=3;}
    if(ip+2>clen){rc=-2;break;}
    int fc=(in[ip]<<8)|in[ip+1];ip+=2;
    if(fc==0)break; if(ip+fc>clen){rc=-10;break;}
    if(fu>ulen-op)fu=ulen-op;
    BR b={in+ip,in+clen,0,0};int fo=0;
    if(!hdr){if(rd(&b,1)){rd(&b,32);}hdr=1;}
    while(fo<fu){
      if(brem==0){
        if(btype==3&&(blen&1))b.p++;
        btype=rd(&b,3);blen=brem=rd(&b,24);
        if(btype==2){for(int i=0;i<8;i++)al[i]=rd(&b,3);build(&at,al,8);}
        if(btype==1||btype==2){
          int nm=256+8*slots;
          if(rlens(&b,ml,0,256)||rlens(&b,ml,256,nm)){rc=-3;goto done;}build(&mt,ml,nm);
          if(rlens(&b,ll,0,LENSZ)){rc=-3;goto done;}build(&lt,ll,LENSZ);
        }else if(btype==3){
          // align: discard to 16-bit boundary
          b.p-= (b.n/16)*2; b.buf=0; b.n=0; if(b.p<in||b.p+12>b.e){rc=-9;goto done;}
          R0=b.p[0]|b.p[1]<<8|b.p[2]<<16|(uint32_t)b.p[3]<<24;b.p+=4;
          R1=b.p[0]|b.p[1]<<8|b.p[2]<<16|(uint32_t)b.p[3]<<24;b.p+=4;
          R2=b.p[0]|b.p[1]<<8|b.p[2]<<16|(uint32_t)b.p[3]<<24;b.p+=4;
        }else{rc=-4;goto done;}
      }
      int todo=fu-fo; if((uint32_t)todo>brem)todo=brem;
      int target=fo+todo;
      if(btype==3){if(b.p<in||b.p+todo>b.e){rc=-8;goto done;}memcpy(out+op+fo,b.p,todo);for(int i=0;i<todo;i++){w[wp]=b.p[i];wp=(wp+1)&wmask;}b.p+=todo;fo+=todo;brem-=todo;continue;}
      while(fo<target){
        int s=dec(&b,&mt);if(s<0){rc=-5;goto done;}
        if(s<256){w[wp]=s;wp=(wp+1)&wmask;out[op+fo++]=s;brem--;continue;}
        s-=256;int ml_=s&7;if(ml_==7){int l=dec(&b,&lt);if(l<0){rc=-6;goto done;}ml_+=l;}ml_+=2;
        int slot=s>>3;uint32_t off;
        if(slot==0)off=R0;else if(slot==1){off=R1;R1=R0;R0=off;}else if(slot==2){off=R2;R2=R0;R0=off;}
        else{int e=ex[slot];
          if(btype==2){
            if(e>3){uint32_t v=rd(&b,e-3)<<3;int a=dec(&b,&at);if(a<0){rc=-7;goto done;}off=base[slot]-2+v+a;}
            else if(e==3){int a=dec(&b,&at);if(a<0){rc=-7;goto done;}off=base[slot]-2+a;}
            else if(e>0)off=base[slot]-2+rd(&b,e);else off=1;
          }else off=base[slot]-2+(e?rd(&b,e):0);
          R2=R1;R1=R0;R0=off;}
        for(int i=0;i<ml_;i++){uint8_t c=w[(wp-off)&wmask];w[wp]=c;wp=(wp+1)&wmask;if(op+fo<ulen)out[op+fo]=c;fo++;}
        brem-=ml_;
      }
    }
    ip+=fc;op+=fu;
  }
done: return rc?rc:op;}
