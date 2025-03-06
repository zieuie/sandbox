// Java program to implement
// the next_permutation method
  
import java.util.Arrays;
import java.util.Random;
import java.io.*;
import java.util.ArrayList;
import java.util.stream.IntStream;
  
public class ChebyshevCustom {
  
  static int n, m, d, u;
  static int N, Nmax=0;
  static int[][] P;
  static int[] ident;
  
  // Function to swap the data
  // present in the left and right indices
  public static int[] swap(int data[], int left, int right)
  {
  
    // Swap the data
    int temp = data[left];
    data[left] = data[right];
    data[right] = temp;
  
    // Return the updated array
    return data;
  }
  
  // Function to reverse the sub-array
  // starting from left to the right
  // both inclusive
  public static int[] reverse(int data[], int left, int right)
  {
  
    // Reverse the sub-array
    while (left < right) {
      int temp = data[left];
      data[left++] = data[right];
      data[right--] = temp;
    }
  
    // Return the updated array
    return data;
  }
  
  // Function to find the next permutation
  // of the given integer array
  public static boolean findNextPermutation(int data[])
  {
  
    // If the given dataset is empty
    // or contains only one element
    // next_permutation is not possible
    //if (data.length <= 1)
    //return false;
  
    //int last = data.length - 2;
    int last = n - 2;
  
    // find the longest non-increasing suffix
    // and find the pivot
    while (last >= 0) {
      if (data[last] < data[last + 1]) {
	break;
      }
      last--;
    }
  
    // If there is no increasing pair
    // there is no higher order permutation
    if (last < 0)
      return false;
  
    //int nextGreater = data.length - 1;
    int nextGreater = n - 1;
  
    // Find the rightmost successor to the pivot
    //for (int i = data.length - 1; i > last; i--) {
    for (int i = n - 1; i > last; i--) {
      if (data[i] > data[last]) {
	nextGreater = i;
	break;
      }
    }
  
    // Swap the successor and the pivot
    data = swap(data, nextGreater, last);
  
    // Reverse the suffix
    //data = reverse(data, last + 1, data.length - 1);
    data = reverse(data, last + 1, n - 1);
  
    // Return true as the next_permutation is done
    return true;
  }
  
  
  public static int dist(int data[]) {
    int di, di1;
    for(int j=0;j<N;j++) {
      di=0;
      for(int i=0;i<n;i++) {
	di1=Math.abs(ident[data[i]]-ident[P[j][i]]);
	if(di<di1) di=di1;
      } 
      if(di<d) return 0;
    }
    return 1;
  }

  public static void PA() {
    int i,i1,j,w;
    int[] data=new int[20];
    P=new int[100000][20];

    Random rand = new Random(System.currentTimeMillis());

    N=0;

    for(j=0;j<u;j++) {
      for(i=0;i<m;i++)
          data[i]=i;
      for(i=0;i<m;i++) {
	i1=rand.nextInt(m-1); //(int) (Math.random()*n);
	w=data[i]; 
	data[i]=data[i1];
	data[i1]=w;
      }
      if(dist(data)>0) {
	for(i=0;i<n;i++) P[N][i]=data[i];
	N++;
      }
    }
    
    //rewrite of Pythons itertools.permutations function
    int[] indices  = IntStream.range(0, m).toArray();
    int[] cycles = new int[n];
    for(i=0; i<n; i++)
        cycles[i] = m-i;
    System.arraycopy(indices, 0, data, 0, n);
    if(dist(data)>0) {
        for(i=0;i<n;i++)
            P[N][i]=data[i];
            N++;
    }
    while(true) {
        completed: {
            for(i=n-1; i>=0; i--) {
                cycles[i] -= 1;
                if(cycles[i] == 0) {
                    int temp = indices[i];
                    int len = m-i-1;
                    System.arraycopy(indices, i+1, indices, i, len);
                    indices[m-1] = temp;
                    cycles[i] = m - i;
                }
                else {
                    j = m-cycles[i];
                    int temp = indices[i];
                    indices[i] = indices[j];
                    indices[j] = temp;
                    System.arraycopy(indices, 0, data, 0, n);
                    if(dist(data)>0) {
                        for(int k=0;k<n;k++)
                            P[N][k]=data[k];
                            N++;
                    }
                    break completed;
                }
            }
            return;
        }
    }
  }

  // Driver Code
  public static void main(String args[])
  {
    
      
    //int data[ = { 4, 3, 2, 1 };
    int[] data=new int[20];
    int i,j;

    /*
        if (!findNextPermutation(data))
            System.out.println("There is no higher"
                               + " order permutation "
                               + "for the given data.");
        else {
            System.out.println(Arrays.toString(data));
        }
    */
    //n = 8;
    //d = 4;
    //u = 0;
    //ident = new int[] {2,3,4,5,6,7,8,9,10};
    n = Integer.parseInt(args[0]);
    d = Integer.parseInt(args[1]);
    u = Integer.parseInt(args[2]);
	ident = Arrays.stream(args[3].split(",")).mapToInt(Integer::parseInt).toArray();
    m = ident.length;
    if(m<n) {
        System.out.println("identity = " + Arrays.toString(ident));
        System.out.println("n = " + n);
        System.out.println("size of identity array must be >= n");
        System.exit(0);
    }
	
    System.out.println("P("+m+","+n+","+d+")");
    System.out.println("identity = " + Arrays.toString(ident));
        
    int ii=0;
    while(true) {
      PA(); 

      if(Nmax<N) { 
	Nmax=N; System.out.println("P("+m+","+n+","+d+")>="+N);

	try {
          File file = new File("P("+m+","+n+","+d+")-"+Arrays.toString(ident)+"-"+String.valueOf(N)+".txt");
          file.createNewFile();
          FileWriter fp = new FileWriter(file);

          for(j=0;j<N;j++) {
            String toWrite = "";
            for(i=0;i<n-1;i++)
                toWrite += String.valueOf(ident[P[j][i]]) + " ";
            toWrite += String.valueOf(ident[P[j][n-1]]) + "\r\n";
            fp.write(toWrite);
          }
          fp.flush();
          fp.close();
        } catch(Exception e){System.out.println(e);}

      }
      
    }  
  }
}