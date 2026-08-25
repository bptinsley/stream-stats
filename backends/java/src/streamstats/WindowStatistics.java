package streamstats;

import java.util.Arrays;

public final class WindowStatistics {
    public static final class WindowException extends Exception {
        public final int status;
        WindowException(int status, String message) { super(message); this.status = status; }
    }
    public record Snapshot(long count, double sum, double min, double max,
                           double mean, double variance, double std) {}
    private static final int NIL = -1;
    private final int capacity;
    private final double[] keys, sums, means, m2, mins, maxes, ring;
    private final long[] multiplicity, counts;
    private final int[] left, right, heights, free;
    private int freeLength, root = NIL, head, length;
    private double lastEvicted;

    public WindowStatistics(int capacity) throws WindowException {
        if (capacity <= 0) throw new WindowException(1, "window size must be positive");
        this.capacity = capacity;
        keys = new double[capacity]; sums = new double[capacity]; means = new double[capacity];
        m2 = new double[capacity]; mins = new double[capacity]; maxes = new double[capacity];
        ring = new double[capacity]; multiplicity = new long[capacity]; counts = new long[capacity];
        left = new int[capacity]; right = new int[capacity]; heights = new int[capacity]; free = new int[capacity];
        Arrays.fill(left, NIL); Arrays.fill(right, NIL);
        for (int i = capacity - 1; i >= 0; --i) free[freeLength++] = i;
    }
    private static double normalized(double value) throws WindowException {
        if (!Double.isFinite(value)) throw new WindowException(1, "value must be finite");
        return value == 0.0 ? 0.0 : value;
    }
    private int height(int node) { return node == NIL ? 0 : heights[node]; }
    private long countAt(int node) { return node == NIL ? 0 : counts[node]; }
    private void pull(int node) {
        int l=left[node], r=right[node]; double key=keys[node]; long own=multiplicity[node];
        long count=l==NIL?0:counts[l]; double mean=l==NIL?0:means[l]; double moment=l==NIL?0:m2[l];
        if(count==0){count=own;mean=key;moment=0;}
        else{long totalCount=count+own;double total=totalCount,delta=key-mean;moment+=delta*delta*count*(own/total);mean=mean*(count/total)+key*(own/total);count=totalCount;}
        if(r!=NIL){long rightCount=counts[r],totalCount=count+rightCount;double total=totalCount,delta=means[r]-mean;moment+=m2[r]+delta*delta*count*(rightCount/total);mean=mean*(count/total)+means[r]*(rightCount/total);count=totalCount;}
        counts[node]=count;means[node]=mean;m2[node]=moment;
        sums[node]=(l==NIL?0:sums[l])+key*multiplicity[node]+(r==NIL?0:sums[r]);
        mins[node]=l==NIL?key:mins[l];maxes[node]=r==NIL?key:maxes[r];
        heights[node]=1+Math.max(height(l),height(r));
    }
    private int acquire(double key) throws WindowException {
        if(freeLength==0)throw new WindowException(5,"node pool exhausted");int n=free[--freeLength];
        keys[n]=key;multiplicity[n]=counts[n]=1;left[n]=right[n]=NIL;heights[n]=1;
        sums[n]=means[n]=mins[n]=maxes[n]=key;m2[n]=0;return n;
    }
    private void release(int n){multiplicity[n]=counts[n]=0;left[n]=right[n]=NIL;heights[n]=0;free[freeLength++]=n;}
    private int balance(int n){return height(left[n])-height(right[n]);}
    private int rotateLeft(int n){int p=right[n],middle=left[p];left[p]=n;right[n]=middle;pull(n);pull(p);return p;}
    private int rotateRight(int n){int p=left[n],middle=right[p];right[p]=n;left[n]=middle;pull(n);pull(p);return p;}
    private int rebalance(int n){pull(n);int b=balance(n);if(b>1){if(balance(left[n])<0)left[n]=rotateLeft(left[n]);return rotateRight(n);}if(b< -1){if(balance(right[n])>0)right[n]=rotateRight(right[n]);return rotateLeft(n);}return n;}
    private int insertAt(int n,double key)throws WindowException{if(n==NIL)return acquire(key);if(key<keys[n])left[n]=insertAt(left[n],key);else if(key>keys[n])right[n]=insertAt(right[n],key);else{multiplicity[n]++;pull(n);return n;}return rebalance(n);}
    private int minimum(int n){while(left[n]!=NIL)n=left[n];return n;}
    private int eraseAt(int n,double key,boolean all)throws WindowException{
        if(n==NIL)throw new WindowException(3,"value not found");
        if(key<keys[n])left[n]=eraseAt(left[n],key,all);else if(key>keys[n])right[n]=eraseAt(right[n],key,all);
        else if(multiplicity[n]>1&&!all){multiplicity[n]--;pull(n);return n;}else{
            int l=left[n],r=right[n];if(l==NIL||r==NIL){int replacement=l==NIL?r:l;release(n);return replacement;}
            int successor=minimum(r);keys[n]=keys[successor];multiplicity[n]=multiplicity[successor];right[n]=eraseAt(r,keys[n],true);
        }return rebalance(n);
    }
    private void insert(double value)throws WindowException{root=insertAt(root,value);}
    private void erase(double value)throws WindowException{root=eraseAt(root,value,false);}
    public boolean add(double value)throws WindowException{
        value=normalized(value);if(length<capacity){insert(value);ring[(head+length)%capacity]=value;length++;return false;}
        double old=ring[head];erase(old);try{insert(value);}catch(WindowException e){insert(old);throw e;}ring[head]=value;head=(head+1)%capacity;lastEvicted=old;return true;
    }
    public double lastEvicted(){return lastEvicted;}
    public double removeOldest()throws WindowException{requireValues();double old=ring[head];erase(old);head=(head+1)%capacity;if(--length==0)head=0;return old;}
    public void remove(double value)throws WindowException{value=normalized(value);int found=-1;for(int i=0;i<length;i++)if(ring[(head+i)%capacity]==value){found=i;break;}if(found<0)throw new WindowException(3,"value not found");erase(value);for(int i=found;i+1<length;i++)ring[(head+i)%capacity]=ring[(head+i+1)%capacity];if(--length==0)head=0;}
    public void clear(){Arrays.fill(multiplicity,0);Arrays.fill(counts,0);Arrays.fill(left,NIL);Arrays.fill(right,NIL);Arrays.fill(heights,0);freeLength=0;for(int i=capacity-1;i>=0;i--)free[freeLength++]=i;root=NIL;head=length=0;}
    private void requireValues()throws WindowException{if(length==0)throw new WindowException(2,"empty window");}
    public int count(){return length;} public double sum(){return root==NIL?0:sums[root];}
    public Snapshot snapshot()throws WindowException{if(length==0)return new Snapshot(0,0,0,0,0,0,0);double variance=m2[root]/length;if(variance<0&&variance>-1e-15)variance=0;if(variance<0)throw new WindowException(5,"negative variance");return new Snapshot(length,sums[root],mins[root],maxes[root],means[root],variance,Math.sqrt(variance));}
    private double select(long rank)throws WindowException{int n=root;while(n!=NIL){long lc=countAt(left[n]);if(rank<lc)n=left[n];else if(rank<lc+multiplicity[n])return keys[n];else{rank-=lc+multiplicity[n];n=right[n];}}throw new WindowException(5,"rank invariant");}
    public double percentile(double p)throws WindowException{requireValues();if(!Double.isFinite(p)||p<0||p>100)throw new WindowException(1,"percentile outside [0,100]");double rank=p/100*(length-1),fraction=rank-Math.floor(rank);long lo=(long)Math.floor(rank),hi=(long)Math.ceil(rank);double a=select(lo);return lo==hi?a:a*(1-fraction)+select(hi)*fraction;}
    public double percentileOf(double value)throws WindowException{requireValues();value=normalized(value);long less=0,equal=0;int n=root;while(n!=NIL){if(value<keys[n])n=left[n];else if(value>keys[n]){less+=countAt(left[n])+multiplicity[n];n=right[n];}else{less+=countAt(left[n]);equal=multiplicity[n];break;}}return 100*(less+.5*equal)/length;}
    private long validateNode(int node, Double lower, Double upper, boolean[] active,
                              double[] values, int[] position)throws WindowException{
        if(node==NIL)return 0;
        if(node<0||node>=capacity||active[node]||multiplicity[node]==0||
           (lower!=null&&keys[node]<=lower)||(upper!=null&&keys[node]>=upper))
            throw new WindowException(5,"AVL ordering/partition invariant");
        active[node]=true;
        long leftCount=validateNode(left[node],lower,keys[node],active,values,position);
        for(long i=0;i<multiplicity[node];i++)values[position[0]++]=keys[node];
        long rightCount=validateNode(right[node],keys[node],upper,active,values,position);
        int expectedHeight=1+Math.max(height(left[node]),height(right[node]));
        long expectedCount=leftCount+multiplicity[node]+rightCount;
        if(Math.abs(height(left[node])-height(right[node]))>1||heights[node]!=expectedHeight||counts[node]!=expectedCount)
            throw new WindowException(5,"AVL metadata invariant");
        return expectedCount;
    }
    public void validate()throws WindowException{
        boolean[] active=new boolean[capacity],freeSeen=new boolean[capacity];double[] tree=new double[length],arrival=new double[length];int[] position={0};
        if(validateNode(root,null,null,active,tree,position)!=length)throw new WindowException(5,"tree count invariant");
        int activeCount=0;for(boolean used:active)if(used)activeCount++;if(activeCount+freeLength!=capacity)throw new WindowException(5,"active/free invariant");
        for(int i=0;i<freeLength;i++){int node=free[i];if(node<0||node>=capacity||active[node]||freeSeen[node])throw new WindowException(5,"free stack invariant");freeSeen[node]=true;}
        for(int i=0;i<length;i++)arrival[i]=ring[(head+i)%capacity];Arrays.sort(arrival);if(!Arrays.equals(tree,arrival))throw new WindowException(5,"tree/ring multiset invariant");
    }
}
