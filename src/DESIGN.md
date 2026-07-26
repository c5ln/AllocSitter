Segregated Free list를 위해 필요한 것

1. Free list에 bin 크기 별로 구분해서 저장해야한다.

2. malloc시 requested bytes에 맞는 bin을 골라서 재활용해야한다.

3. free시 free된 byte 크기에 맞는 bin에 넣어야 한다.

4. spliting은 유지한다.
   - bin에 맞는 블록이 없으면 더 큰 bin에서 꺼내 split하고, 나머지는 나머지 크기에 맞는 bin에 push한다.
   - class 경계를 촘촘하게 잡을수록 split 빈도는 줄어든다.

5. coalescing은 작은 class에서는 하지 않는다.
   - 이 워크로드는 요청의 99.7%가 64~127에 몰려 있어, free된 블록이 같은 크기로 곧장 재사용된다. 병합해도 다음 malloc에서 도로 split하게 되므로 낭비다.
   - coalescing을 없애면 이웃 블록을 bin에서 빼내는 arbitrary remove(O(n) 병목, free당 53 remove_steps)가 필요 없어져 free가 O(1) push가 된다.

6. realloc의 in-place 확장(오른쪽 free 이웃 흡수하기)은 포기한다. 커질 땐 항상 malloc + memcpy + free.
   - 이 워크로드에서 realloc은 드물어서 복사 비용은 무시할 수 있다.
  