package streamstats.scala

import java.util.Arrays

final class WindowException(val status: Int, message: String) extends Exception(message)
final case class Snapshot(count: Long, sum: Double, min: Double, max: Double,
                          mean: Double, variance: Double, std: Double)

final class WindowStatistics(val capacity: Int):
  private val NilIndex = -1
  if capacity <= 0 then throw WindowException(1, "window size must be positive")
  private val keys = Array.ofDim[Double](capacity)
  private val sums = Array.ofDim[Double](capacity)
  private val means = Array.ofDim[Double](capacity)
  private val m2 = Array.ofDim[Double](capacity)
  private val mins = Array.ofDim[Double](capacity)
  private val maxes = Array.ofDim[Double](capacity)
  private val ring = Array.ofDim[Double](capacity)
  private val multiplicity = Array.ofDim[Long](capacity)
  private val counts = Array.ofDim[Long](capacity)
  private val left = Array.fill(capacity)(NilIndex)
  private val right = Array.fill(capacity)(NilIndex)
  private val heights = Array.ofDim[Int](capacity)
  private val free = Array.ofDim[Int](capacity)
  private var freeLength = 0
  private var root = NilIndex
  private var head = 0
  private var length = 0
  private var evictedValue = 0.0
  locally:
    var i = capacity - 1
    while i >= 0 do
      free(freeLength) = i
      freeLength += 1
      i -= 1

  private def normalize(value: Double): Double =
    if !java.lang.Double.isFinite(value) then throw WindowException(1, "value must be finite")
    if value == 0.0 then 0.0 else value
  private def height(n: Int): Int = if n == NilIndex then 0 else heights(n)
  private def countAt(n: Int): Long = if n == NilIndex then 0 else counts(n)
  private def pull(n: Int): Unit =
    val l=left(n);val r=right(n);val key=keys(n);val own=multiplicity(n)
    var count=if l==NilIndex then 0L else counts(l)
    var mean=if l==NilIndex then 0.0 else means(l)
    var moment=if l==NilIndex then 0.0 else m2(l)
    if count == 0 then
      count = own
      mean = key
      moment = 0.0
    else
      val total=count+own;val delta=key-mean
      moment += delta*delta*count*(own.toDouble/total)
      mean = mean*(count.toDouble/total)+key*(own.toDouble/total)
      count = total
    if r != NilIndex then
      val rc=counts(r);val total=count+rc;val delta=means(r)-mean
      moment += m2(r)+delta*delta*count*(rc.toDouble/total)
      mean=mean*(count.toDouble/total)+means(r)*(rc.toDouble/total);count=total
    counts(n)=count;means(n)=mean;m2(n)=moment
    sums(n)=(if l==NilIndex then 0.0 else sums(l))+key*own+(if r==NilIndex then 0.0 else sums(r))
    mins(n)=if l==NilIndex then key else mins(l);maxes(n)=if r==NilIndex then key else maxes(r)
    heights(n)=1+math.max(height(l),height(r))
  private def acquire(key: Double): Int =
    if freeLength==0 then throw WindowException(5,"node pool exhausted")
    freeLength-=1;val n=free(freeLength);keys(n)=key;multiplicity(n)=1;counts(n)=1
    left(n)=NilIndex;right(n)=NilIndex;heights(n)=1;sums(n)=key;means(n)=key;m2(n)=0;mins(n)=key;maxes(n)=key;n
  private def release(n:Int):Unit =
    multiplicity(n)=0;counts(n)=0;left(n)=NilIndex;right(n)=NilIndex;heights(n)=0;free(freeLength)=n;freeLength+=1
  private def balance(n:Int):Int=height(left(n))-height(right(n))
  private def rotateLeft(n:Int):Int=
    val p=right(n);val middle=left(p);left(p)=n;right(n)=middle;pull(n);pull(p);p
  private def rotateRight(n:Int):Int=
    val p=left(n);val middle=right(p);right(p)=n;left(n)=middle;pull(n);pull(p);p
  private def rebalance(n:Int):Int=
    pull(n);val b=balance(n)
    if b>1 then
      if balance(left(n))<0 then left(n)=rotateLeft(left(n))
      rotateRight(n)
    else if b < -1 then
      if balance(right(n))>0 then right(n)=rotateRight(right(n))
      rotateLeft(n)
    else n
  private def insertAt(n:Int,key:Double):Int=
    if n==NilIndex then acquire(key)
    else
      if key<keys(n) then left(n)=insertAt(left(n),key)
      else if key>keys(n) then right(n)=insertAt(right(n),key)
      else
        multiplicity(n)+=1
        pull(n)
        return n
      rebalance(n)
  private def minimum(start:Int):Int=
    var n=start
    while left(n)!=NilIndex do n=left(n)
    n
  private def eraseAt(n:Int,key:Double,all:Boolean):Int=
    if n==NilIndex then throw WindowException(3,"value not found")
    if key<keys(n) then left(n)=eraseAt(left(n),key,all)
    else if key>keys(n) then right(n)=eraseAt(right(n),key,all)
    else if multiplicity(n)>1 && !all then
      multiplicity(n)-=1
      pull(n)
      return n
    else
      val l=left(n)
      val r=right(n)
      if l==NilIndex || r==NilIndex then
        val replacement=if l==NilIndex then r else l
        release(n)
        return replacement
      val successor=minimum(r)
      keys(n)=keys(successor)
      multiplicity(n)=multiplicity(successor)
      right(n)=eraseAt(r,keys(n),true)
    rebalance(n)
  private def insert(value:Double):Unit=root=insertAt(root,value)
  private def erase(value:Double):Unit=root=eraseAt(root,value,false)
  def add(raw:Double):Boolean=
    val value=normalize(raw)
    if length<capacity then
      insert(value)
      ring((head+length)%capacity)=value
      length+=1
      false
    else
      val old=ring(head)
      erase(old)
      try insert(value)
      catch
        case error:WindowException =>
          insert(old)
          throw error
      ring(head)=value
      head=(head+1)%capacity
      evictedValue=old
      true
  def lastEvicted:Double=evictedValue
  private def requireValues():Unit=if length==0 then throw WindowException(2,"empty window")
  def removeOldest():Double=
    requireValues()
    val old=ring(head)
    erase(old)
    head=(head+1)%capacity
    length-=1
    if length==0 then head=0
    old
  def remove(raw:Double):Unit=
    val value=normalize(raw)
    var found = -1
    var i=0
    while i<length && found<0 do
      if ring((head+i)%capacity)==value then found=i
      i+=1
    if found<0 then throw WindowException(3,"value not found")
    erase(value)
    i=found
    while i+1<length do
      ring((head+i)%capacity)=ring((head+i+1)%capacity)
      i+=1
    length-=1
    if length==0 then head=0
  def clear():Unit=
    Arrays.fill(multiplicity,0L);Arrays.fill(counts,0L);Arrays.fill(left,NilIndex);Arrays.fill(right,NilIndex);Arrays.fill(heights,0)
    freeLength=0
    var i=capacity-1
    while i>=0 do
      free(freeLength)=i
      freeLength+=1
      i-=1
    root=NilIndex;head=0;length=0
  def count:Int=length
  def snapshot():Snapshot=
    if length==0 then Snapshot(0,0,0,0,0,0,0)
    else
      var variance=m2(root)/length
      if variance<0 && variance > -1e-15 then variance=0
      if variance<0 then throw WindowException(5,"negative variance")
      Snapshot(length,sums(root),mins(root),maxes(root),means(root),variance,math.sqrt(variance))
  private def select(rankRaw:Long):Double=
    var rank=rankRaw
    var n=root
    while n!=NilIndex do
      val lc=countAt(left(n))
      if rank<lc then n=left(n)
      else if rank<lc+multiplicity(n) then return keys(n)
      else
        rank-=lc+multiplicity(n)
        n=right(n)
    throw WindowException(5,"rank invariant")
  def percentile(p:Double):Double=
    requireValues()
    if !java.lang.Double.isFinite(p) || p<0 || p>100 then throw WindowException(1,"invalid percentile")
    val rank=p/100*(length-1);val lo=math.floor(rank).toLong;val hi=math.ceil(rank).toLong;val a=select(lo)
    if lo==hi then a
    else
      val f=rank-lo
      a*(1-f)+select(hi)*f
  def percentileOf(raw:Double):Double=
    requireValues()
    val value=normalize(raw)
    var less=0L
    var equal=0L
    var n=root
    while n!=NilIndex do
      if value<keys(n) then n=left(n)
      else if value>keys(n) then
        less+=countAt(left(n))+multiplicity(n)
        n=right(n)
      else
        less+=countAt(left(n))
        equal=multiplicity(n)
        n=NilIndex
    100*(less+.5*equal)/length
  private def validateNode(n:Int,lower:Option[Double],upper:Option[Double],active:Array[Boolean],values:Array[Double],position:Array[Int]):Long=
    if n==NilIndex then return 0
    if n<0 || n>=capacity || active(n) || multiplicity(n)==0 ||
       lower.exists(bound => keys(n)<=bound) || upper.exists(bound => keys(n)>=bound)
    then throw WindowException(5,"AVL ordering/partition invariant")
    active(n)=true
    val leftCount=validateNode(left(n),lower,Some(keys(n)),active,values,position)
    var i=0L
    while i<multiplicity(n) do
      values(position(0))=keys(n)
      position(0)+=1
      i+=1
    val rightCount=validateNode(right(n),Some(keys(n)),upper,active,values,position)
    val expectedHeight=1+math.max(height(left(n)),height(right(n)))
    val expectedCount=leftCount+multiplicity(n)+rightCount
    if math.abs(height(left(n))-height(right(n)))>1 || heights(n)!=expectedHeight || counts(n)!=expectedCount then throw WindowException(5,"AVL metadata invariant")
    expectedCount
  def validate():Unit=
    val active=Array.ofDim[Boolean](capacity)
    val freeSeen=Array.ofDim[Boolean](capacity)
    val tree=Array.ofDim[Double](length)
    val arrival=Array.ofDim[Double](length)
    val position=Array(0)
    if validateNode(root,None,None,active,tree,position)!=length then throw WindowException(5,"tree count invariant")
    var activeCount=0
    var i=0
    while i<capacity do
      if active(i) then activeCount+=1
      i+=1
    if activeCount+freeLength!=capacity then throw WindowException(5,"active/free invariant")
    i=0
    while i<freeLength do
      val n=free(i)
      if n<0 || n>=capacity || active(n) || freeSeen(n) then throw WindowException(5,"free stack invariant")
      freeSeen(n)=true
      i+=1
    i=0
    while i<length do
      arrival(i)=ring((head+i)%capacity)
      i+=1
    Arrays.sort(arrival)
    if !Arrays.equals(tree,arrival) then throw WindowException(5,"tree/ring multiset invariant")
