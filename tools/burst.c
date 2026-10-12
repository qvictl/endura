// burst: measure how fast short bursts of UI-like work complete from idle.
// Usage: burst <calib|run> [iters]. calib prints loops per ms at current state.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <string.h>
static double now(){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e3+t.tv_nsec/1e6;}
static volatile unsigned long sink;
static void work(unsigned long n){unsigned long x=88172645463325252UL;for(unsigned long i=0;i<n;i++){x^=x<<13;x^=x>>7;x^=x<<17;} sink=x;}
static int cmp(const void*a,const void*b){double d=*(double*)a-*(double*)b;return d<0?-1:d>0;}
int main(int c,char**v){
  unsigned long lpm = c>2? strtoul(v[2],0,10):0; // loops per ms at full speed
  if(!strcmp(v[1],"calib")){ // spin 2s to reach max clock, then measure
    work(400000000UL); double t=now(); work(200000000UL); double d=now()-t; printf("%lu\n",(unsigned long)(200000000UL/d)); return 0;}
  int sizes[]={2,10,50}; int iters= c>3?atoi(v[3]):30;
  for(int s=0;s<3;s++){ double r[200];
    for(int i=0;i<iters;i++){ usleep(150000+ (rand()%100000)); double t=now(); work(lpm*sizes[s]); r[i]=now()-t; }
    qsort(r,iters,sizeof(double),cmp);
    printf("%dms-unit: p50=%.2fms p90=%.2fms max=%.2fms slowdown_p50=%.2fx\n",sizes[s],r[iters/2],r[iters*9/10],r[iters-1],r[iters/2]/sizes[s]);
  }
}
