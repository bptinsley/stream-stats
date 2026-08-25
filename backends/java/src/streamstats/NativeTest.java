package streamstats;

public final class NativeTest {
    public static void main(String[] args)throws Exception{
        WindowStatistics w=new WindowStatistics(3);for(double v:new double[]{1,2,3,4})w.add(v);
        WindowStatistics.Snapshot s=w.snapshot();if(s.count()!=3||s.sum()!=9||Math.abs(s.variance()-2.0/3)>1e-12)throw new AssertionError();
        if(w.percentile(50)!=3)throw new AssertionError();w.validate();
    }
}
