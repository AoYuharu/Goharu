# Metadata

- **title**: ACM-GNN: Adaptive Cluster-Oriented Modularity Graph Neural Network for EEG Depression Detection
- **author**: 
- **subject**: IEEE Transactions on Computational Social Systems;2026;13;1;10.1109/TCSS.2025.3576373
- **creator**: LaTeX with hyperref
- **producer**: Adobe LiveCycle PDF Generator; modified using iText® Core 7.2.4 (AGPL version) ©2000-2022 iText Group NV
- **creation_date**: D:20260129214049+05'30'
- **modification_date**: D:20260212112604-05'00'
- **page_count**: 13
- **file_size**: 3215501
## Page 1

208
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
ACM-GNN: Adaptive Cluster-Oriented Modularity
Graph Neural Network for EEG
Depression Detection
Tong Zhang
, Senior Member, IEEE, Tingting Hu
, Student Member, IEEE,
Mengqi Wu
, Student Member, IEEE, Zihua Xu
, Student Member, IEEE,
and C. L. Philip Chen
, Life Fellow, IEEE
Abstract—Major depressive disorder (MDD) is typically ac-
companied by varying topological dynamics across brain network
modularity due to the inﬂuence of time-variant and subject-
speciﬁc factors. Current works primarily characterize the elec-
troencephalograms (EEG) topological relationships based on
prior predeﬁned brain regions. However, this predeﬁned strategy
cannot dynamically ﬁt to different individuals, which may affect
the adaptability of unseen individual for depression detection.
This article proposes an adaptive cluster-oriented modularity
graph neural network (ACM-GNN) to enhance the adaptability of
individual topological interaction for depression detection. Specif-
ically, a cluster-oriented modularity construction (CMC) module
dynamically clusters EEG channels into different brain regions
based on channel-pairs contrastive learning. It can adaptively
construct brain modularity to ﬁt different individual instances.
Furthermore, a modularity graph interaction learning (MGIL)
module performs multilayer graph information interaction be-
tween EEG globality and modularity levels. In this way, more
powerful hierarchical information can be integrated by further
aggregating representations at different levels. Experiments on
two public datasets, MODMA and PRED+CT, demonstrate
that the proposed method outperforms the state-of-the-art EEG
depression detection methods. Finally, investigations on brain
activities reveal the importance of dynamic modular relations
for depression detection.
Received 15 November 2024; revised 19 May 2025; accepted 30 May 2025.
Date of publication 17 July 2025; date of current version 6 February 2026.
This work was supported in part by the National Natural Science Foundation
of China under Grant 62222603, Grant 62076102, and Grant 92267203; in
part by the STI2030-Major Projects grant from the Ministry of Science and
Technology of the People’s Republic of China under Grant 2021ZD0200700;
in part by the Key-Area Research and Development Program of Guang-
dong Province under Grant 2023B0303030001; in part by the Program for
Guangdong Introducing Innovative and Entrepreneurial Teams under Grant
2019ZT08X214; and in part by the Science and Technology Program of
Guangzhou under Grant 2024A04J6310. (Corresponding author: C. L. Philip
Chen.)
The authors are with the Guangdong Provincial Key Laboratory of Com-
putational AI Models and Cognitive Intelligence, the School of Computer
Science and Engineering, South China University of Technology, Guangzhou
510006, China, also with the Pazhou Lab, Guangzhou 510335, China, and also
with Engineering Research Center of the Ministry of Education on Health In-
telligent Perception and Paralleled Digital-Human, Guangzhou 510000, China
(e-mail: Philip.chen@ieee.org).
Digital Object Identiﬁer 10.1109/TCSS.2025.3576373
Index Terms—Brain network modularity, deep graph cluster-
ing, EEG depression detection, graph interaction learning (GIL).
I. INTRODUCTION
M
AJOR depressive disorder (MDD) is a common mental
illness [1]. According to the incomplete statistics of
the World Health Organization, 280 million people worldwide
suffer from MDD [2]. MDD patients often experience persistent
low mood, loss of interest, severe fatigue, and feelings of help-
lessness and hopelessness, all of which have a signiﬁcant impact
on their mental health [3], [4]. Additionally, they may suffer
from cognitive impairment, sleep disturbances, and changes in
appetite [5]. Therefore, early detection of depression is crucial
for timely intervention to control or reduce this mental illness.
In the clinical diagnosis of MDD, physicians and psychia-
trists use questionnaire-based assessments and professional in-
terviews to evaluate the severity [6], [7]. However, the traditional
approach relies heavily on the cooperation and awareness be-
tween physicians and patients. To overcome the interference of
these subjective factors, physiological signals such as electroen-
cephalograms (EEG) [8], [9], electromyograms (EMG) [10],
and electrocardiograms (ECG) [11] are widely used in depres-
sion detection. Due to the high temporal resolution, noninvasive-
ness, and low cost characteristics of EEG [12]. EEG-based MDD
detection can effectively utilize the dynamics of brain activity to
reveal the neural patterns associated with depression.
Some pioneering works utilized machine learning to extract
hand-crafted features, perform feature selection, and enhance
depression detection [13], [14]. Speciﬁcally, support vector ma-
chines (SVM), random forests (RF), and k-nearest neighbors
(KNN) were employed to detect brain patterns in the EEG
signals of MDD patients. Nonetheless, the efﬁcacy of these
algorithms is highly dependent on the quality of hand-crafted
features and the sufﬁcient MDD-related prior knowledge [15],
[16]. In recent years, deep learning has been applied to EEG
for feature extraction and depression detection in an end-to-end
way [17], [18], [19]. Beneﬁting from the ability to effectively
capture spatial features and handle temporal dynamics, convo-
lutional neural networks (CNN) and long short-term memory
networks (LSTM) are widely utilized for EEG-based depression
2329-924X © 2025 IEEE. All rights reserved, including rights for text and data mining, and training of artiﬁcial intelligence and similar technologies.
Personal use is permitted, but republication/redistribution requires IEEE permission. See https://www.ieee.org/publications/rights/index.html for more information.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 2

ZHANG et al.: ACM-GNN: ADAPTIVE CLUSTER-ORIENTED MODULARITY
209
identiﬁcation. As the EEG electrodes are placed in a spheri-
cal pattern, CNN-based methods have difﬁculty handling non-
Euclidean data. According to the research by [20], it is pivotal
to capture the underlying interactions between channels for
depression detection. Graph neural network (GNN) can effec-
tively leverage spatial structure and topological information in
irregular data [21], [22]. Several studies attempted to learn the
intrinsic interactions between channels by constructing EEG
topological connections based on GNN [23], [24].
According to the literature [25], depression disorders are
closely related to functional abnormalities in the modularity
of the brain network. Morphological differences were observed
between several brain regions in people with depression com-
pared with healthy individuals. Thus, some research has fo-
cused on exploring the complex topological interactions among
multiple predeﬁned brain regions. Zhang et al. [26] divided
EEG channels into eight nonoverlapping modules to explore
the neuro-mechanisms of patients with depression. Some stud-
ies [27], [28] also revealed signiﬁcant differences in brain
synchronization between patients with depression and normal
controls in certain brain regions, such as the left frontal, tempo-
ral, parieto-occipital, and right temporal lobes. However, these
predeﬁned brain regions are ﬁxed based on prior knowledge,
which cannot dynamically adapt to different individuals for de-
pression detection. Compared with healthy individuals, patients
may exhibit individual-speciﬁc brain patterns due to the high
dynamics in brain network modularity [29], [30]. It is crucial to
further reﬁne brain regions to enhance the adaptability of brain
modularity against the individual differences [31]. Additionally,
topological EEG representations from local to global levels
are essential for extracting powerful hierarchical information.
Therefore, this article aims to explore an adaptive brain network
modularity that accommodates individual speciﬁcity and con-
tains rich hierarchical information to enhance the adaptability
of MDD detection algorithms.
Motivated by the above analysis, an adaptive cluster-oriented
modularity graph neural network (ACM-GNN) is proposed to
address the insufﬁcient ﬂexibility and adaptability in model-
ing individual brain regions. First, a cluster-oriented modularity
construction (CMC) module is designed to construct brain net-
work modularity to adapt to the dynamics of different individual
brain modularity. Speciﬁcally, channel-pair contrastive learning
in CMC module enhances the stability of modularity construc-
tion by constraining the distances of channel pairs within and
across clusters. Subsequently, a modularity graph interaction
learning (MGIL) module is designed to learn multi-layer graph
information interactions between the globality and modular-
ity levels. In this way, more powerful hierarchical information
from the whole brain can be further integrated by aggregating
dual levels of representations. Finally, discriminative representa-
tions are obtained through feature space mapping for depression
detection.
The main contributions of this article are as follows.
1) ACM-GNN is proposed to enhance the adaptability of
individual topological interactions for depression detec-
tion. This approach adapts to interindividual differences,
enabling more effective EEG-based depression detection.
2) The CMC module is proposed to dynamically cluster
EEG channels into different brain regions using channel-
pair contrastive learning. This process is essential for
constructing brain network modularity that changes with
different individual instances.
3) The MGIL module is designed to perform multi-layer
graph information interactions between EEG globality-
and modularity-levels, further obtaining powerful hier-
archical information by aggregating different levels of
representations.
II. RELATED WORK
A. EEG-Based Depression Detection
Traditional machine learning methods for EEG-based de-
pression detection usually require manual feature extraction
[32], [33]. Common feature extraction methods can be di-
vided into time-domain features and frequency-domain fea-
tures. Some graph features based on complex network theory
can describe the network topology of EEG signals [34]. Liu
et al. [35] extracted features such as spectral power, Lempel–
Ziv complexity, and detrended ﬂuctuation analysis for MDD
feature selection. These approaches rely on experience and prior
knowledge, which may limit their ability to uncover potential
information in complex data. Recently, research has focused on
using deep learning methods for end-to-end depression detec-
tion. Seal et al. [16] proposed an 18-layer deep convolutional
neural network to automatically learn EEG data, considering
both spatial and temporal information. Betul et al. [36] de-
signed a hybrid model that combined CNN and LSTM to detect
depression. Furthermore, DeprNet [16] has demonstrated its
remarkable prowess by achieving convincing results through a
comprehensive approach that takes into account both spatial and
temporal information. BrainNet [37] also focuses on spatiotem-
poral features. In the complex realm of data analysis and feature
extraction, understanding the spatial aspects helps in discerning
the relationships and arrangements of different elements within
a given dataset, while considering the temporal information
allows for capturing the changes and sequences over time. On
the other hand, HybridEEGNet [38] is an innovative neural
network architecture that has been meticulously designed with
convolutional ﬁlters. These convolutional ﬁlters play a pivotal
role in learning synchronous and regional features.
Despite the success of these methods, CNN-based methods
may not fully capture the topological structure in irregular graph
signals. Therefore, this article adopts a GCN-based network to
better capture both global and local information and topological
structures.
B. Graph-Based Depression Recognition
Graph convolutional network (GCN) performs convolution
operations on graph structures to effectively learn node relation-
ships and structural information [39], [40]. It has been widely
applied in ﬁelds such as social network analysis, recommenda-
tion systems, and trafﬁc ﬂow prediction, achieving signiﬁcant
results [41], [42].
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 3

210
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
GCNs can be applied to EEG data by treating electrodes
as vertices and their interconnections as edges. This approach
helps identify activity patterns across different cerebral regions
[43]. Zhu et al. [44] proposed GICN, which utilizes the brain
functional network as the adjacency matrix and linear features
as node inputs, and incorporates a weight matrix in the graph
input attention layer to determine the importance of each edge
for depression recognition. Chen et al. [45] proposed SGP-SL, a
graph-based model that explores local and global connections
among EEG channels and constructs an EEG-based graph. It
reﬁnes the graph using multiple self-attention graph pooling
modules, aggregating information from less-important to more-
important nodes. Li et al. [46] proposed GCNs-FSMI, which
integrates pretrained graph convolutional networks and graph
mutual information maximization to explore high-level inter-
actions in multichannel EEG data. This method investigates
ﬁne-grained EEG data and the correlations between EEG and
mental illnesses. SSPA-GCN [47] combines GCN and domain
generalization technology to enhance the performance.
Despite the great potential of GCN methods for exploring
the topological structure of EEG data, most overlook individ-
ual differences in brain networks. In contrast, the proposed
ACM-GNN employs a personalized brain region partitioning
approach, using data-driven clustering algorithms to identify
and partition individual brain functional regions based on spe-
ciﬁc data and features. This approach captures individual differ-
ences and reveals complex connections between different brain
regions.
C. Brain Network Modularity Dynamics
Research has highlighted that modularity is a key aspect
of brain network architecture, revealing the fundamental or-
ganizational form of the brain [20]. Modularity suggests that
cerebral functions are executed through the coordinated efforts
of multiple functional modules [48].
Due to signiﬁcant diversity in the EEG intensity distribution
across different brain regions, several models leverage modu-
larity characteristics to process EEG signals. Cui et al. [49] split
the features into four local tokens based on corresponding brain
regions and proposed a region-attention feature fusion network
(RAFFNet) to apply different attention weights to these regions.
According to neuroscience evidence. Ding et al. [50] proposed
LGGNet to divide different functional brain regions and model
the complex relations within and among these areas. Addition-
ally, they introduced a frontal LGG deﬁnition and a hemisphere
deﬁnition based on frontal asymmetry patterns and symmet-
rical principles. Gong et al. [51] utilized prior knowledge to
divide EEG channels into 13 different regions and proposed
an adaptive region selection method to explicitly explore the
most emotion-related active brain regions, thereby alleviating
the feature redundancy problem in EEG signals. In MFMR-FN
[52], there is a multi-region decoding network, including three
branches, capturing not only coarse-grained FC features at the
whole-brain scale, but also extracting ﬁne-grained features from
the hemispheres and local brain regions.
Fig. 1.
Varying topological dynamics across brain network modularity.
Although these methods perform well with modularity char-
acteristics, manually selected brain region divisions struggle to
capture complex neural mechanisms and individual differences.
Varying topological dynamics across brain network modular-
ity are shown in Fig. 1, illustrating that the electrode com-
position of brain modules is dynamically changing. There-
fore, a data-driven method is proposed to adaptively construct
subject-speciﬁc brain regions, addressing the issue of individual
variability.
III. METHOD
This section introduces the proposed ACM-GNN, which in-
cludes key components: a cluster-oriented modularity construc-
tion (CMC) module that dynamically clusters EEG channels
into regions to obtain multilevel spatial information, a modu-
larity graph interaction learning (MGIL) module that captures
graph information at both globality and modularity levels and
facilitates information interaction, and a ﬁnal stage that uses
the feature representations for MDD detection. The overall
framework is illustrated in Fig. 2.
A. Notations
A weighted undirected graph is denoted as G = {V, E, A},
where V represents the set of nodes, E represents the edges
between nodes, and A is the adjacency matrix describing the re-
lationships between nodes. Given the raw data of EEG signals,
a globality-level graph GC = {VC, EC, AC} can be obtained,
where each EEG channel serves as a node, and the relationships
between channels are represented by the adjacency matrix. The
feature matrix of the globality-level graph is represented as
XC ∈RN×d, with N representing the number of electrode
channels and d representing the dimension of input features.
B. Cluster-Oriented Modularity Construction Module
For EEG-based MDD detection, predeﬁned nonoverlapping
modules may lead to a lack of individual-speciﬁc dynamic
information. To explore the functional relationship between
depressive patterns and brain network modularity, a cluster-
oriented modularity construction module is proposed to adap-
tively construct brain regions, which enhances adaptability to
individual differences.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 4

ZHANG et al.: ACM-GNN: ADAPTIVE CLUSTER-ORIENTED MODULARITY
211
Fig. 2.
Overall framework of the ACM-GNN. In the CMC module, the modularity construction is adaptively updated for clustering. In the MGIL module,
transfer and aggregation operations are designed to facilitate the interaction between globality and modularity information.
Inspired by contrastive learning, whole-brain channels are
fed into unshared encoders to capture two augmented views of
spatial embeddings. Speciﬁcally, the general encoder is used
to learn the embedding of the general view, which can be
represented as follows:
E1 = EG(XC)
(1)
where EG(·) represents using an MLP to encode the feature
matrix XC for constructing the general node embeddings.
To obtain a robust view, the robust encoder is constructed
by introducing Gaussian perturbations to the node embeddings.
The process of learning the robust view is described as follows:
E2 = ER(XC) + N
(2)
where ER(·) represents using an MLP to encode the feature
matrix XC for constructing the robust node embeddings. By
adding Gaussian noise N ∈RN×d sampled from N(0, 1) to the
encoder, the model is forced to capture subtle variations in the
representations. This operation can enhance the capability of
the robust encoder to handle randomness and variability in node
embeddings. In summary, two augmented views of diverse node
embeddings are constructed by employing encoders with the
same structure but different parameters.
To integrate cross-view information, a high-dimensional fu-
sion method is employed to process the two augmented views.
The aggregation of node embeddings from the general view E1
and the robust view E2 is formulated as follows:
E = F(E1, E2)
(3)
where F(·) denotes linear combination operation.
Morphological differences are observed in multiple brain
regions between depression patients and healthy individuals.
Similarly, signiﬁcant variability in dynamics is also observed
among individuals. To address the poor generalization caused
by high dynamics in brain network modularity, individual-
speciﬁc brain patterns are extracted by performing an adap-
tive node clustering. By mining the intrinsic relationships of
node implicit features to partition nodes into different clus-
ters to represent brain modularity, the clustering process is
formalized as
M C = K(E)
(4)
in which K(·) employs the K-means algorithm [53] to compute
M C as the node partitions. The binary cluster matrix M C ∈
RN indicates the assignment of the jth channel to the elements
M Cj. The construction of the cluster matrix inherently sup-
ports brain functional segregation and integration.
Intramodule nodes should exhibit similar attributes or fea-
tures, while intermodule nodes should possess distinctly dif-
ferent attributes or features. This phenomenon is consistent
with the objectives of contrastive learning. Consequently, con-
trastive learning is introduced to dynamically update EEG chan-
nel assignments. Positive and negative pairs are constructed to
tightly cluster similar instances together in the latent space,
while nodes with greater differences are mapped to more dis-
tant positions. The cluster-oriented contrastive pairs are fully
formed using the relationship matrix M R ∈RN×N, which is
formalized as follows:
M Rij =

1,
if M Ci = M Cj
0,
if M Ci ̸= M Cj
(5)
in which M R is used to explicitly construct positive and neg-
ative pairs. When M Rij = 1, the ith and jth channels are
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 5

212
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
assigned to the same brain region. Conversely, M Rij = 0 in-
dicates that nodes are associated with different brain regions.
i, j = 1, 2, ..., N.
To adaptively update the modularity construction of EEG
samples, a clustering-oriented contrastive loss is designed to
learn the similarities and differences among the samples. This
loss can be expressed as follows:
Lc = 1
N 2

(S −M R)2.
(6)
The similarity matrix S ∈RN×N of the samples between two
augmented views is computed as
Sij = E1i ·

E2j
T
(7)
where the Sij quantiﬁes the cosine similarity between the ith
node embedding from the general view and the jth node embed-
ding from the robust view. (·)T indicates transposition. i, j =
1, 2, ..., N. Minimizing the clustering-oriented contrastive loss
pulls nodes from the same brain region closer together in the
embedding space and pushes nodes from different regions far-
ther apart, treating same-region nodes as positive pairs and
different-region nodes as negative pairs.
Through the above steps, feature subsets representing the
modularized representation of whole-brain channels are pro-
duced as follows:
XRi =
1
| {j : M Cj = i} |

M Cj =i
XCj
(8)
in which | {j : M Cj = i} | represents the number of channels
associated with the ith brain modularity. The original channel
features XC are transformed into a modularized features set
XR ∈RNr×d via the cluster matrix. Nr is the number of brain
regions. i represents the index of brain regions, and j denotes
the index of nodes. i = 1, 2, ..., Nr, j = 1, 2, ..., N.
C. Modularity Graph Interaction Learning Module
Local and global representations are crucial for extracting ro-
bust hierarchical information. Multi-layer modular graph learn-
ing is designed to capture topological representations. Interac-
tions between different levels are then conducted to achieve a
reﬁned graph representation.
1) Globality and Modularity Graph Learning: To inves-
tigate an adaptive brain network modularity that incorporates
individual speciﬁcity and hierarchical information to enhance
performance. For graph feature extraction, the feature matrix
XC is input into graph convolutional networks (GCNs) as
follows:
Z(l+1) = Gconv(Z(l), ˜A)
= σ(˜LZ(l)W (l))
= σ( ˜D−1
2 ˜A ˜D−1
2 Z(l)W (l))
(9)
where Z(l) represents the feature matrix of the graph at layer l
and the initial state Z(0) = X ∈RN×d. ˜A ∈RN×N is the adja-
cency matrix of the globality-level graph with self-loops, which
is randomly initialized and is learnable during the training
process. ˜D−(1/2) ˜A ˜D−(1/2) is Laplacian regularization, which
denotes symmetric normalization of the adjacency matrix. ˜D is
the degree matrix of ˜A. W (l) is the weighting matrix of the lth
layer, σ is the activation function.
To alleviate the over-smoothing issue in globality-level graph
convolution and enhance the learnable dynamics of spatial con-
nections, a sparse and trainable adjacency matrix is employed
to improve the adaptability of spatial connections. The formula
is as follows:
˜ACS = σ(W2δ(W1, ˜AC))
(10)
in which ˜AC is vectorized to the shape RN×N and transformed
through two linear layers.
Based on the excellent multilayer message-passing mecha-
nism of graph convolutional networks, which gradually expands
the receptive ﬁeld to capture global structural representations,
global graph learning is introduced to capture the global rela-
tionships among different EEG signal channels. The formula
can be derived as follows:
Z(l+1)
C
= Gconv(Z(l)
C , ˜ACS).
(11)
Similarly, a sparse and trainable module-level adjacency ma-
trix can be calculated as follows:
˜ARS = σ(W4δ(W3, ˜AR))
(12)
in which ˜AR ∈RNr×Nr is randomly initialized and learn-
able during the training process, and then fed into two linear
transformations.
Depression is closely related to functional abnormalities in
the modularity of the brain network. Modularity graph learning
is designed to dynamically extract the topological relationships
and spatial information of different modules as a modularity-
level graph. The operation can be expressed as follows:
Z(l+1)
R
= Gconv(Z(l)
R , ˜ARS)
(13)
where Z(l)
R is the feature matrix of the modularity-level graph
at layer l, and Z(0)
R = XR ∈RNr×d.
2) Dual-Level Interaction: The brain can ﬂexibly switch
between local and global information processing modes. Func-
tional segregation and integration, fundamental to cognitive
behaviors, are closely tied to cognitive task complexity and
brain diseases. Thus, the interaction between globality and
modularity-based information is crucial. Modularity graph
learning can lead to information loss due to coarse-grained
aggregation, so interactions between different levels are used
to mitigate this issue.
After obtaining the feature representations of both the global
and modular graphs through multilayer graph learning, the dual-
level information is further interacted to mitigate information
loss, thereby improving the performance of EEG-based depres-
sion recognition. To maintain consistency between the global
and modular feature spaces, a feature interaction operation is
employed to inject global features into the modular features.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 6

ZHANG et al.: ACM-GNN: ADAPTIVE CLUSTER-ORIENTED MODULARITY
213
The formula is as follows:
Z(l)
INTER = ft(Z(l)
C , M C)
=
1
| {j : M Cj = i} |

M Cj =i
ZCj
(14)
in which i = 1, 2, ..., Nr, j = 1, 2, ..., N.
Furthermore, the interaction features and modularity-based
features are integrated, the process can be described as follows:
Z(l)
Rout = fa(Z(l)
R , Z(l)
INTER).
(15)
Through the above steps, the globality and modularity-
based features for depression recognition are fully interactively
learned.
D. MDD Detection
After completing the hierarchical graph convolution of the
two branches, the ﬁnal representation ZC and ZR of each
branch can be obtained respectively. Then, the globality-level
graph representation is concatenated with the modularity-level
graph representation to obtain the ﬁnal feature representation
of the network
Z = concat(ZC, ZR).
(16)
Finally, the features Z are ﬂatten into a one-dimensional
vector and input them into two linear layers to obtain the ﬁnal
detection results YP.
Consequently, the classiﬁcation loss can be expressed as
follows:
Lcla = −
c

i=1

Yi log YP
i

(17)
where Y and YP denote the real label vector and the predicted
one, respectively. c is the number of samples.
Then, the total loss function can be expressed as
L = λ · Lc + μ · Lcla
(18)
where λ and μ are the coefﬁcients of Lc and Lcla, respectively,
which are assigned by the automatic adaptive weight method
[54].
IV. EXPERIMENTS AND RESULTS
In this section, the datasets are introduced, the effectiveness
of the proposed model is validated on these datasets, and the
ablation study is presented.
A. Datasets
1) MODMA Dataset: The MODMA dataset [55], released
by the UAIS laboratory at Lanzhou University in 2020, includes
EEG and audio data from 24 clinical depression patients and 29
normal controls. Subjects were recruited from the Second Hos-
pital of Lanzhou University and through posters, with diagnoses
conﬁrmed by professional psychiatrists. Our experiments use
128-channel EEG signals recorded in a resting state, sampled
at 250 Hz for 5 min.
2) PRED+CT Dataset: The PRED+CT dataset [56] consists
of EEG recordings from 43 depressed patients and 43 healthy
controls, recruited based on Beck depression inventory (BDI)
scores from an introductory psychology course. The dataset
includes 66-channel EEG data sampled at 500 Hz, with partic-
ipants providing informed consent. Only subjects with at least
5 min of EEG data were included.
3) TDBRAIN Dataset:
The TDBRAIN dataset contains
resting-state EEG recordings from individuals with psychiatric
disorders,
including
major
depressive
disorder
(MDD),
attention-deﬁcit/hyperactivity
disorder
(ADHD),
and
obsessive-compulsive disorder (OCD). EEG signals were
recorded from 26 electrodes according to the 10-10 system at
a sampling rate of 500 Hz. In our experiments, we selected
individuals formally diagnosed with MDD as MDD samples,
and individuals without an indication of MDD as control
samples, resulting in a total of 380 subjects.
B. Experimental Settings
1) Data Preprocessing: Following previous studies, we seg-
ment the raw EEG signal into nonoverlapping 2-s segments and
treat these segments as independent samples to collect sufﬁcient
training samples. Speciﬁcally, in the MODMA dataset, there
are 3600 segments for depression patients (24 subjects * 150
segments) and 4350 segments for the normal control group
(29 subjects * 150 segments). In the PRED+CT dataset, there
are 6450 segments for depression patients (43 subjects * 150
segments) and 6450 segments for the normal control group (43
subjects * 150 segments). In the TDBRAIN dataset, 7260 (132
subjects * 55 segments) MDD samples and 13640 (248 subjects
* 55 segments) NC samples are obtained.
2) Validation Strategy: To assess the efﬁcacy of the pro-
posed model in EEG-based depression detection, we employ
leave-one-subject-out (LOSO) validation on MODMA and
PRED+CT dataset. One subject is utilized as testing data, while
the remaining subjects are used as training data. For TDBRAIN
dataset, we conducted 10-fold cross validation due to too many
subjects. Given that the dataset contains 380 subjects, 38 sub-
jects are selected to test in each fold. The conclusive result is
the mean outcome across all subject/fold for each dataset.
C. Implemental Details
Experiments are conducted on a CentOS 7 platform, harness-
ing the computational prowess of an NVIDIA GTX 2080 Ti
graphics processing unit. The model is instantiated within the
PyTorch deep learning framework, with the computational envi-
ronment comprising Python 3.9.15, CUDA 11.1, and PyTorch
1.9.1. For the parameters designed in the model, the number
of brain regions is set to 10 for the MODMA dataset, 8 for
the PRED+CT dataset and 5 for the TDBRAIN dataset, with
2 stacked graph convolutional layers. Training parameters are
conﬁgured with a batch size of 256, a learning rate of 0.0001,
and epoch counts of 10 for the MODMA/TDBRAIN dataset and
20 for the PRED+CT dataset, respectively. The Adam optimizer
is employed for the optimization of the model.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 7

214
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
D. Experimental Results
1) Results on MODMA Dataset: To evaluate the perfor-
mance of the proposed model, it is utilized to perform de-
pression detection tasks on the MODMA dataset along with
other baseline methods. The selected baseline methods include
traditional machine learning classiﬁers such as support vector
machine (SVM), conventional neural networks such as CNN,
networks speciﬁcally designed for EEG classiﬁcation such as
EEGNet [57], and networks predicated on graph data structures
such as GCN. Moreover, the model is benchmarked against
several established methods applied to the MODMA dataset.
For fairness, models that utilize an equivalent number of sub-
jects and the same validation techniques are selected: mK-
TAChSel+SVM [32], self-organised TPTLP-based model [33],
SGP-SL [45], BrainNet+CNN+GRU [37], SSPA-GCN [47] and
MFMR-FN [52]. Below, these models are described brieﬂy.
1) mKTAChSel+SVM: An optimal channel selection method
via kernel-target alignment (KTA). A modiﬁed version
KTA is proposed to measure the similarity between
the kernel matrix and the target matrix as an objective
function.
2) Self-Organised TPTLP-Based Model: A self-organized
computationally lightweight handcrafted classiﬁcation
model. A novel Twin Pascal’s triangles lattice pattern
(TPTLP) is proposed to extract local textural features.
3) SGP-SL: A GNN-based method exploring local and
global connections among EEG channels. Multiple self-
attention graph pooling modules are leveraged to learn
discriminative feature representation.
4) BrainNet+CNN+GRU: A method based on spatiotempo-
ral features. Power spectra of frequency ranges is com-
puted to obtain brain maps with spatial information. CNN
and GRU are applied to extract the sequential feature.
5) SSPA-GCN: A model with a secondary subject partition-
ing and attention mechanism based on GCN. Domain
generalization based on adversarial training is added to
enhanced the performance.
6) MFMR-FN: An interpretable network that fuses mul-
tiple frequency bands to capture shallow and deep
functional connectivity, and introduces a multi-region
selection mechanism to realize coarse-to-ﬁne emotion
decoding.
As indicated in Table I, the proposed method surpasses
all baseline and existing methods applied to the MODMA
dataset across every evaluative metric. Speciﬁcally, the pro-
posed method achieves a classiﬁcation accuracy of 95.46%,
demonstrating the model’s overall correct prediction capability.
Furthermore, the F1-score, precision, and recall metrics reach
95.80%, 96.23%, and 95.46%, respectively, also outperforming
all other methods, indicating that the proposed model’s perfor-
mance is balanced across all aspects. The experimental results
outperform other graph-structured networks, with an accuracy
improvement of 2.6% over the state-of-the-art model, indicating
the effectiveness of the proposed method.
2) Results on PRED+CT Dataset: Owing to the variability
in participant numbers and validation methodologies in extant
TABLE I
COMPARISON OF CLASSIFICATION RESULTS BETWEEN DIFFERENT MODELS
ON MADMA DATASET
Model
Acc(%)
F1(%)
Pre(%)
Rec(%)
p-value
SVM
59.55
55.87
55.21
56.54
0.0001*
CNN
63.94
58.19
61.26
55.42
0.0003*
EEGNet
64.34
61.60
60.11
63.17
0.0001*
Ss-GCN
73.83
70.23
72.42
68.17
0.0003*
mKTAChSel(2020)
81.60
89.97
-
-
0.0024*
TPTLP(2023)
83.96
81.10
86.76
76.14
0.0077*
SGP+SL(2022)
84.91
84.00
80.77
87.50
0.0052*
BrainMap(2022)
89.63
90.19
-
90.24
0.0010*
SSPA-GCN(2024)
92.87
92.12
92.23
92.00
0.0015*
MFMR-FN(2025)
93.96
93.97
-
94.95
0.0084*
ACM-GNN (ours)
95.46
95.80
96.23
95.46
-
Note: * means the superiority of ACM-GNN over the comparison methods is
statistically signiﬁcant at the level of 0.05. Bold values are indicate the best
results.
TABLE II
COMPARISON OF CLASSIFICATION RESULTS BETWEEN DIFFERENT MODELS
ON PRED+CT DATASET
Model
Acc(%)
F1(%)
Pre(%)
Rec(%)
p-value
SVM
52.74
54.73
52.52
57.14
0.0010*
CNN
54.79
54.95
54.76
55.14
0.0005*
EEGNet
55.56
55.96
55.46
56.47
0.0006*
Ss-GCN
73.50
73.17
74.09
72.28
0.0023*
MSTGCN(2021)
77.02
80.23
75.39
77.74
0.0152*
SSPA-GCN(2024)
83.17
82.93
84.15
81.74
0.0149*
ACM-GNN (ours)
89.55
91.25
98.81
89.55
-
Note: * means the superiority of ACM-GNN over the comparison methods is
statistically signiﬁcant at the level of 0.05. Bold values are indicate the best
results.
studies on the PRED+CT dataset, comparisons are drawn ex-
clusively with baseline models SVM, CNN, EEGNet, Ss-GCN,
MSTGCN, and SSPA-GCN, which use the same experimental
setup. As shown in Table II, the results on the PRED+CT
dataset indicate that the proposed method outperforms all other
methods. Speciﬁcally, it attains a classiﬁcation accuracy of
89.55%, with F1-score, precision, and recall metrics reaching
91.25%, 98.81%, and 89.55%, respectively, surpassing all base-
line methods. Furthermore, in contrast to the state-of-the-art
SSPA-GCN model, an enhancement in classiﬁcation accuracy,
F1-score, precision, and recall by 6.38%, 8.32%, 14.66%, and
7.81%, respectively is observed, demonstrating the efﬁcacy of
the approach. Notably, MSTGCN and SSPA-GCN employed
additional domain generalization techniques, but the proposed
method still outperformed these approaches in cross-subject
experimental settings, demonstrating the superiority of ACM-
GNN in adapting to interindividual differences.
3) Results on TDBRAIN Dataset:
On the TDBRAIN
dataset, we compared the proposed method with the baseline
methods using ten-fold cross-validation, including SVM, CNN,
EEGNet, Ss-GCN. Table III shows that the proposed method
achieves 73.44% in accuracy, which is better than all base-
line methods. For the validation metrics F1-score, precision,
and recall, the proposed model achieves 73.03%, 72.21%, and
73.86%, respectively, which validates the model’s balanced as-
pects. TDBRAIN contains only a relatively small number of
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 8

ZHANG et al.: ACM-GNN: ADAPTIVE CLUSTER-ORIENTED MODULARITY
215
TABLE III
COMPARISON OF CLASSIFICATION RESULTS BETWEEN DIFFERENT MODELS
ON TDBRAIN DATASET
Model
Acc(%)
F1(%)
Pre(%)
Rec(%)
p-value
SVM
50.67
51.97
55.31
48.43
0.0012*
CNN
66.19
66.90
70.23
67.84
0.0190*
EEGNet
72.98
71.40
72.15
70.16
0.0171*
Ss-GCN
71.76
69.99
71.06
70.06
0.0286*
ACM-GNN (ours)
73.44
73.03
72.21
73.86
-
Note: * means the superiority of ACM-GNN over the comparison methods is
statistically signiﬁcant at the level of 0.05. Bold values are indicate the best
results.
26 electrodes, which may make the delineation of each brain
module contain insufﬁcient information and affect the model
performance. Under this condition, the proposed model still
achieved good performance, proving the generalization ability
of the model.
Compared with other methods, the proposed model surpasses
traditional handcrafted feature classiﬁcation and CNN-based
models. It is validated that graph-based models can effectively
capture the topological structure between EEG channels. More-
over, the proposed model considers the modularity informa-
tion of EEG signals and adaptively discovers potential brain
region divisions. Globality-level and modularity-level informa-
tion are integrated to capture the brain activity patterns of differ-
ent individuals more comprehensively, which enhances model
performance.
Furthermore, statistical tests were performed using paired-
sample t-tests to evaluate the signiﬁcance of differences be-
tween the experimental results and the comparison methods.
The p-values consistently remained below 0.05, indicating that
the proposed model outperformed the comparison methods with
statistically signiﬁcant differences.
4) Confusion Matrix: To demonstrate the classiﬁcation ca-
pability of ACM-GNN, confusion matrices are computed for
the method on both the MODMA and PRED+CT datasets, illus-
trating the comparison between the model’s predictions and the
actual results. As shown in Fig. 3, in the MODMA dataset, for
the normal control group, the model’s classiﬁcation accuracy is
98%, with a false positive rate of 2%. For depression patients,
the model’s classiﬁcation accuracy is 92%, with a false negative
rate of 8%. Overall, the model performs well for both groups,
with relatively better performance for the normal control group.
The higher false negative rate for depression patients might be
due to the smaller sample size of depression patients, which
introduces some bias. For the depression detection scenario,
bias towards any class in the model is undesirable, as this could
lead to the omission of potential patients in need of intervention
or result in misdiagnosis. However, existing methods tend to
favor the majority class and neglect the minority class. Com-
pared with other methods, the proposed method demonstrates
robustness in handling the minority class.
E. Ablation Study
In this section, the ablation study is conducted to verify the
effectiveness of each component in the model. The results are
shown in Table IV.
(a)
(b)
Fig. 3.
Confusion matrices of the proposed ACM-GNN. (a) Confusion
matrix on MODMA dataset. (b) Confusion matrix on PRED+CT dataset. The
horizontal axis represents the true labels, and the vertical axis represents the
predicted labels.
1) W/O One of the Branches: Firstly, the impact of the
globality-level graph branch is validated. The “w/o globality-
level graph” method learns only the modularity-level features
for the ﬁnal classiﬁcation task and shows a performance de-
crease on both datasets. It suggests that the information pro-
vided in the globality-level graphs constructed from the original
EEG signals cannot be ignored. At the same time, the “w/o
modularity-level graph” method removes the brain modularity-
level graph branches and the performance is reduced. It suggests
that the modularity-level features also provide useful infor-
mation for depression identiﬁcation, and compensate for the
EEG topology that the globality-level graph convolution cannot
capture. This result is consistent with the modularity principle
of the brain structure.
2) W/O CMC Module: To verify the effectiveness of adap-
tive modularity construction, the “w/o adaptive modularity”
method replaces it with a ﬁxed modularity partition. The results
show a decrease in performance, indicating that the proposed
adaptive brain modularity construction method is more rea-
sonable. Compared with manually deﬁned modularity, it can
better capture modularity information within the complex brain
network mechanisms. The “w/o cluster relation matrix” method
removes the cluster relation matrix and uses only an identify
matrix to deﬁne positive and negative pairs. The decrease in
accuracy validates that utilizing cluster relation to construct
positive and negative pairs assists the model’s modular cogni-
tive capabilities in the process of learning node representations.
3) W/O Dual-level Interaction: The “w/o dual-level inter-
action” method removes the interaction between the globality-
level graph and the modularity-level graph, resulting in a
decrease in performance. It indicates that integrating globality-
level feature representations into modularity-level feature rep-
resentations effectively compensates for potential information
loss during the aggregation of modularity-level information.
V. ANALYSIS
A. Comparison With Predeﬁned Modularity Construction
To further substantiate the efﬁcacy of the adaptive brain
modularity construction, a comparison is conducted with more
predeﬁned modularity methods. The nodes are partitioned into
predeﬁned 4, 8, and 17 modularity, respectively, following the
methods mentioned in the literature [26], [49], [58].
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 9

216
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
TABLE IV
EXPERIMENTAL RESULTS OF ABLATION STUDY ON MODMA AND PRED+CT DATASET
Model
MODMA dataset
PRED+CT dataset
Acc(%)
F1(%)
Pre(%)
Rec(%)
Acc(%)
F1(%)
Pre(%)
Rec(%)
w/o globality-level graph
83.17
83.63
84.68
82.61
68.92
68.65
69.89
67.46
w/o modularity-level graph
85.75
85.55
86.39
84.72
70.14
71.69
73.44
70.03
w/o CMC module
86.12
86.65
87.44
85.87
76.56
77.72
78.94
76.53
w/o cluster relation matrix
89.66
89.29
89.86
88.73
76.96
79.17
80.22
78.14
w/o dual-level interaction
92.96
93.13
92.83
93.44
82.94
82.29
84.49
80.21
ACM-GNN
95.46
95.80
96.23
95.46
89.55
91.25
98.81
89.55
Note: Bold values are indicate the best results.
(a)
(b)
(c)
(d)
Fig. 4.
Different predeﬁned modularity methods and their corresponding
evaluation metrics compared with CMC in MODMA dataset. (a) Predeﬁned
4. (b) Predeﬁned 8. (c) Predeﬁned 17. (d) Evaluation metrics.
Fig. 4(a)–4(c) shows the speciﬁc predeﬁned modularity par-
tition on 128 EEG electrode cap layouts with MODMA dataset,
and Fig. 4(d) shows the corresponding evaluation metrics com-
pared with CMC. The results indicate that the predeﬁned four
modularity model exhibits the poorest performance, while the
predeﬁned 8 and 17 modularity models demonstrate improved
performance. Moreover, The proposed CMC mothed achieves
the best results. The results suggest that ﬁner module partition-
ing enhances performance, but is less effective than the pro-
posed adaptive partitioning approach, validating that dynamic
clustering is essential for constructing brain network modularity
that changes with different individual instances. Fig. 5(a)–5(c)
shows the predeﬁned modularity partition on 66 EEG electrode
cap layouts with PRED+CT dataset, and Fig. 5(d) shows the
corresponding evaluation metrics. The results indicate that the
predeﬁned 4, 8, and 17 modularity methods exhibit similar
performance, while the proposed adaptive partition method still
achieves the best results. This suggests that with fewer EEG
electrodes, ﬁne-grained predeﬁned partitioning has limited im-
pact on improving model performance, whereas the proposed
(a)
(b)
(c)
(d)
Fig. 5.
Different predeﬁned modularity methods and their corresponding
evaluation metrics compared with CMC in PRED+CT dataset. (a) Predeﬁned
4. (b) Predeﬁned 8. (c) Predeﬁned 17. (d) Evaluation metrics.
adaptive partitioning method continues to demonstrate strong
performance.
B. T-SNE Visualization
To demonstrate the feature extraction capability and the ul-
timate classiﬁcation performance of the model, the data dis-
tribution is visualized before and after model training. The
t-distributed stochastic neighbor embedding (t-SNE) technique
is employed to illustrate the distribution of the original data, the
features of the globality-level graph branch, and the modularity-
level graph branch, as well as the ﬁnal feature representation.
Results on the MODMA dataset are shown in Fig. 6. Fig. 6(a)
displays the distribution of the original data. It is observed that
the distribution of data points is dispersed, lacking a distinct
clustering structure. Fig. 6(b) reveals the feature distribution of
the globality-level graph branch. It indicates a partial clustering
structure as the MDD group’s points are mainly distributed on
the left side. This suggests that the globality-level graph branch
effectively utilizes the spatial relationship of the EEG channels
and extracts effective features.
Nonetheless, there is still a considerable overlap between
the data points of the two categories. Fig. 6(c) represents the
feature distribution of the modularity-level graph branch. It
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 10

ZHANG et al.: ACM-GNN: ADAPTIVE CLUSTER-ORIENTED MODULARITY
217
Fig. 6.
t-SNE visualisation on MODMA dataset. (a) Original state. (b) State after the globality-level graph branch. (c) State after modularity-level graph
branch. (d) Final state.
Fig. 7.
t-SNE visualisation on PRED+CT dataset. (a) Original state. (b) State after the globality-level graph branch. (c) State after modularity-level graph
branch. (d) Final state.
shows that the MDD group’s points are mainly distributed in
the middle of the graph, while the points of the NC group
are mainly distributed in the upper left and lower right, with
reduced overlap. This demonstrates the capability of adaptive
modularity construction. Fig. 6(d) presents the model’s ﬁnal
feature representation. It shows a more compact intraclass struc-
ture and a more distinct inter-class boundary. This indicates
that the features obtained by combining the two branches in
the proposed model are more effective.
Fig. 7 shows a similar result on the PRED+CT dataset.
Speciﬁcally, in the initial state, the data exhibits a scattered
and disordered distribution. After training the globality-level
graph branch, data points with different labels display a certain
degree of separation. Following training with the modularity-
level graph branch, the distribution of data points becomes
more concentrated, and the separation between data points with
different labels becomes more distinct. In the ﬁnal state, a
clear boundary between the distributions of the MDD group
and the NC group can be observed. These results demonstrate
the feature extraction capability and the ultimate classiﬁcation
performance of the proposed model.
C. Impact of Brain Clustering Strategies
The impact of varying numbers of regions in the brain clus-
tering strategy is essential to the model’s performance. In neu-
roscience, the human brain is conventionally segmented into
four principal regions: the frontal, parietal, temporal, and oc-
cipital lobes. The experiments started with four clusters and
the number of clusters gradually increased up to 10. In this
way, the brain is adaptively divided into different numbers of
regions, and a series of experiments are conducted to eval-
uate the model’s performance. The experimental results on
MODMA dataset, as illustrated in Fig. 8, indicate a clear trend
in the impact of different numbers of clusters on the model’s
Fig. 8.
Performance of the different number of clusters.
performance. Speciﬁcally, as the number of clusters increases,
the overall performance of the model shows an upward trend,
reaching the best performance when the number of clusters is
10. This suggests that a more detailed division of the brain
can capture richer and ﬁner information about brain regions. It
enables the model to more accurately reﬂect the complex func-
tional structure of the brain, thereby improving the accuracy and
reliability of the model.
On PRED+CT dataset, the impact of different numbers of
clusters on the model’s performance shows a similar trend, with
the best performance achieved when the number of clusters is
8. When the number of clusters exceeds 8, the model’s per-
formance declines. This suggests that over-segmentation may
cause each cluster to contain too little data, making it difﬁcult
for the model to learn useful features from each region.
The different performance of the model on the two datasets
is attributed to the amount of information in the data.
The MODMA dataset includes 128 channels, whereas the
PRED+CT dataset includes 66 channels. The higher number
of channels in the MODMA dataset provides a greater amount
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 11

218
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
(a)
(b)
(c)
(d)
(e)
(f)
Fig. 9.
Top 3 modularity learned by the proposed method. Each color
represents a modularity, and the importance of these colors is as follows:
orange, green, and blue. (a) MDD 1. (b) MDD 2. (c) MDD 3. (d) NC 1. (e)
NC 2. (f) NC 3.
of information, which requires a more extensive cluster con-
ﬁguration to effectively ﬁlter and extract valuable modularity
information.
D. Distribution of Dynamic Brain Modularity
The above experiments verify the good performance of the
proposed model on the EEG depression detection task and also
show the effectiveness of the adaptive brain modularity con-
struction method. To directly demonstrate the brain modularity
constructed using the adaptive method, visualization results is
provided. Degree centrality within the modularity-level graph is
used to measure the importance of modularity, which refers to
the strength of connections between each node and other nodes
in the adjacency matrix. It is calculated as follows:
Ci =
nr

j=1
(Ai,j) +
nr

k=1
(Ak,i) −2Ai,i.
(19)
Fig. 9 illustrates the top three modularity with the highest
degree of centrality across depression and normal control indi-
viduals on the MODMA dataset. The illustration reveals that the
brain modularity constructed through the adaptive method ex-
hibits distinctions from the traditionally deﬁned manually par-
titioned brain modularity. Individual speciﬁcity of brain modu-
larity exists in both depression individuals and normal control
individuals. A modularity may consist not only of channels
with close positions but also of other more complex connec-
tions, which are overlooked by manual partitioning methods.
Moreover, the composition of brain modularity varies among
different individuals and may be composed of different channel
locations and number of channels. These variations cannot be
captured by the ﬁxed brain modularity partitioning method.
Additionally, the study ﬁnds that the important modules
that contribute the most are variable in normal individuals,
while there are some commonalities in depression individuals.
The pivotal modularity mainly concentrates on the frontal and
temporal lobes, which align with previous ﬁndings regarding
brain connectivity in patients with depression [59], [60]. This
is because the frontal lobe is involved in executive control and
emotion regulation, while the temporal lobe is associated with
auditory processing and memory functions. In major depres-
sive disorder, dysfunctions in these lobes have been repeatedly
reported [61], [62]. Furthermore, a symmetrical distribution of
channel positions within the brain modularity has also been ob-
served, consistent with the hemispheric symmetric connections
veriﬁed in existing literature [45].
VI. CONCLUSION
In this work, an ACM-GNN is proposed to investigate the
topological attributes of EEG signals for the detection of de-
pression. The CMC module dynamically groups EEG channels
into modularity to capture multilevel spatial information. Fur-
thermore, by learning relationships between global and mod-
ular levels via graph convolutional networks and interacting
between these levels, rich discriminative features for depression
detection are extracted. Experimental results on the MODMA
and PRED+CT datasets validate the effectiveness of ACM-
GNN, with visualizations demonstrating the constructed brain
modularity. Future work will integrate brain modularity func-
tional analysis with the interpretability of graph convolutional
networks to explore the roles and importance of different
brain modules in depression and provide interpretable model
explanations.
REFERENCES
[1] T. Chen, R. Hong, Y. Guo, S. Hao, and B. Hu, “Ms2-GNN: Exploring
GNN-based multimodal fusion network for depression detection,” IEEE
Trans. Cybern., vol. 53, no. 12, pp. 7749–7759, Dec. 2023.
[2] S. E. Beable, “Depressive disorders in athletes,” Clinics Sports Med.,
vol. 43, no. 1, pp. 53–70, 2024.
[3] L. Cui et al., “Major depressive disorder: hypothesis, mechanism,
prevention and treatment,” Signal Transduction Targeted Therapy, vol. 9,
no. 1, p. 30, 2024.
[4] K. A. Johnson, M. S. Okun, K. W. Scangos, H. S. Mayberg, and C.
de Hemptinne, “Deep brain stimulation for refractory major depressive
disorder: a comprehensive review,” Mol. Psychiatry, vol. 29, no. 4,
pp. 1–13, 2024.
[5] Z. Jia et al., “High-ﬁeld magnetic resonance imaging of suicidality in
patients with major depressive disorder,” Amer. J. Psychiatry, vol. 167,
no. 11, pp. 1381–1390, 2010.
[6] N. Cummins, V. Sethu, J. Epps, J. R. Williamson, T. F. Quatieri, and
J. Krajewski, “Generalized two-stage rank regression framework for
depression score prediction from speech,” IEEE Trans. Affect. Comput.,
vol. 11, no. 2, pp. 272–283, Apr./Jun. 2017.
[7] F. Hao, G. Pang, Y. Wu, Z. Pi, L. Xia, and G. Min, “Providing
appropriate social support to prevention of depression for highly anxious
sufferers,” IEEE Trans. Computat. Social Syst., vol. 6, no. 5, pp. 879–
887, Oct. 2019.
[8] X. Li, C. P. Chen, B. Chen, and T. Zhang, “Gusa: Graph-based
unsupervised subdomain adaptation for cross-subject EEG emotion
recognition,” IEEE Trans. Affect. Comput., vol. 15, no. 3, pp. 1451–
1462, Jul./Sep. 2024.
[9] Z. Zhang, Y. Liu, and S-h Zhong, “GANSER: A self-supervised data
augmentation framework for EEG-based emotion recognition,” IEEE
Trans. Affect. Comput., vol. 14, no. 3, pp. 2048–2063, Jul./Sep. 2022.
[10] B. Cheng and G. Liu, “Emotion recognition from surface EMG signal
using wavelet transform and neural network,” in Proc. 2nd Int. Conf.
Bioinf. and Biomed. Eng., Piscataway, NJ, USA: IEEE Press, 2008,
pp. 1363–1366.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 12

ZHANG et al.: ACM-GNN: ADAPTIVE CLUSTER-ORIENTED MODULARITY
219
[11] F. Agraﬁoti, D. Hatzinakos, and A. K. Anderson, “ECG pattern analysis
for emotion detection,” IEEE Trans. Affect. Comput., vol. 3, no. 1,
pp. 102–115, Jan./Mar. 2012.
[12] F. S. de Aguiar Neto and J. L. G. Rosa, “Depression biomarkers
using non-invasive EEG: A review,” NeuroSci. BioBehav. Rev., vol. 105,
pp. 83–93, Oct. 2019.
[13] X. Li, B. Hu, S. Sun, and H. Cai, “EEG-based mild depressive detection
using feature selection methods and classiﬁers,” Comput. Methods
Programs Biomed., vol. 136, pp. 151–161, Nov. 2016.
[14] R. A. Movahed, G. P. Jahromi, S. Shahyad, and G. H. Meftahi, “A
major depressive disorder classiﬁcation framework based on EEG signals
using statistical, spectral, wavelet, functional connectivity, and nonlinear
analysis,” J. Neurosci. Methods, vol. 358, 2021, Art. no. 109209.
[15] H. Cai, X. Sha, X. Han, S. Wei, and B. Hu, “Pervasive EEG diagnosis
of depression using deep belief network with three-electrodes EEG
collector,” in Proc. IEEE Int. Conf. Bioinf. and bioMed. (BIBM),
Piscataway, NJ, USA: IEEE Press, 2016, pp. 1239–1246.
[16] A. Seal, R. Bajpai, J. Agnihotri, A. Yazidi, E. Herrera-Viedma, and O.
Krejcar, “DeprNet: A deep convolution neural network framework for
detecting depression using EEG,” IEEE Trans. Instrum. Meas., vol. 70,
pp. 1–13, 2021.
[17] T. Zhang, Q. Li, J. Wen, and C. Philip Chen, “Enhancement and
optimisation of human pose estimation with multi-scale spatial attention
and adversarial data augmentation,” Inf. Fusion, vol. 111, 2024, Art. no.
102522.
[18] F. Tian et al., “The three-lead EEG sensor: Introducing an EEG-assisted
depression diagnosis system based on ant lion optimization,” IEEE
Trans. Biomed. Circuits Syst., vol. 17, no. 6, pp. 1305–1318, Dec. 2023.
[19] D. Peng, W. Liu, Y. Luo, Z. Mao, W.-L. Zheng, and B.-L. Lu, “Deep
depression detection with resting-state and cognitive-task EEG,” in Proc.
45th Annu. Int. Conf. IEEE Eng. Med. Biol. Soc. (EMBC), 2023, pp. 1–4.
[20] G. S. Wig, “Segregated systems of human brain networks,” Trends Cogn.
Sci., vol. 21, no. 12, pp. 981–996, 2017.
[21] K. Huang, Y. Jin, E. Candes, and J. Leskovec, “Uncertainty quantiﬁca-
tion over graph with conformalized graph neural networks,” in Advances
in Neural Information Processing Systems, A. Oh, T. Naumann, A.
Globerson, K. Saenko, M. Hardt, and S. Levine, Eds., vol. 36. New
Orleans, LA, USA: Curran Associates, Inc., 2023, pp. 26699–26721.
[22] J. Fang, et al., “Evaluating post-hoc explanations for graph neural
networks via robustness analysis,” in Advances in Neural Information
Processing Systems, A. Oh, T. Naumann, A. Globerson, K. Saenko, M.
Hardt, and S. Levine, Eds., vol. 36. New Orleans, LA, USA: Curran
Associates, Inc., 2023, pp. 72446–72463.
[23] T. Zhao and G. Zhang, “Enhancing major depressive disorder diagnosis
with dynamic-static fusion graph neural networks,” IEEE J. Biomed.
Health Inform., vol. 28, no. 8, pp. 4701–4710, Aug. 2024.
[24] Y. Li et al., “GMSS: Graph-based multi-task self-supervised learning for
EEG emotion recognition,” IEEE Trans. Affect. Comput., vol. 14, no. 3,
pp. 2512–2525, Jul./Sep. 2022.
[25] L. Dai, H. Zhou, X. Xu, and Z. Zuo, “Brain structural and functional
changes in patients with major depressive disorder: a literature review,”
PeerJ, vol. 7, 2019, Art. no. e8170.
[26] B. Zhang, H. Cai, Y. Song, L. Tao, and Y. Li, “Computer-aided recog-
nition based on decision-level multimodal fusion for depression,” IEEE
J. Biomed. Health Inform., vol. 26, no. 7, pp. 3466–3477, Jul. 2022.
[27] K. A. Lindquist, T. D. Wager, H. Kober, E. Bliss-Moreau, and L. F.
Barrett, “The brain basis of emotion: a meta-analytic review,” Behav.
Brain Sci., vol. 35, no. 3, pp. 121–143, 2012.
[28] B. Zhang, G. Yan, Z. Yang, Y. Su, J. Wang, and T. Lei, “Brain functional
networks based on resting-state EEG data for major depressive disorder
analysis and classiﬁcation,” IEEE Trans. Neural Syst. Rehabil. Eng.,
vol. 29, pp. 215–229, Mar. 2021.
[29] S. Genon, S. B. Eickhoff, and S. Kharabian, “Linking interindividual
variability in brain structure to behaviour,” Nat. Rev. Neurosci., vol. 23,
no. 5, pp. 307–318, 2022.
[30] B. Chen, C. L. P. Chen, and T. Zhang, “Ugan: Uncertainty-guided
graph augmentation network for EEG emotion recognition,” IEEE Trans.
Comput. Social Syst., vol. 12, no. 2, pp. 695–707, Apr. 2025.
[31] H. Chen et al., “Resting-state EEG dynamic functional connectivity dis-
tinguishes non-psychotic major depression, psychotic major depression
and schizophrenia,” Mol. Psychiatry, vol. 29, no. 4, pp. 1–11, 2024.
[32] J. Shen et al., “An optimal channel selection for EEG-based depression
detection via kernel-target alignment,” IEEE J. Biomed. Health Inform.,
vol. 25, no. 7, pp. 2545–2556, Jul. 2021.
[33] G. Tasci et al., “Automated accurate detection of depression using twin
pascal’s triangles lattice pattern with EEG signals,” Knowl.-Based Syst.,
vol. 260, 2023, Art. no. 110190.
[34] M. Wu, C. L. P. Chen, B. Chen, and T. Zhang, “Grop: Graph orthogonal
puriﬁcation network for EEG emotion recognition,” IEEE Trans. Affect.
Comput., vol. 16, no. 1, pp. 319–332, Jan./Mar. 2025.
[35] S. Liu et al., “Alterations in patients with ﬁrst-episode depression in the
eyes-open and eyes-closed conditions: A resting-state EEG study,” IEEE
Trans. Neural Syst. Rehabil. Eng., vol. 30, pp. 1019–1029, Apr. 2022.
[36] B. Ay et al., “Automated depression detection using deep representation
and sequence learning with EEG signals,” J. Med. Syst., vol. 43, pp. 1–
12, May 2019.
[37] W. Liu, K. Jia, Z. Wang, and Z. Ma, “A depression prediction algorithm
based on spatiotemporal feature of EEG signal,” Brain Sci., vol. 12,
no. 5, p. 630, 2022.
[38] Z. Wan, J. Huang, H. Zhang, H. Zhou, J. Yang, and N. Zhong, “Hy-
bridEEGNet: A convolutional neural network for EEG feature learning
and depression discrimination,” IEEE Access, vol. 8, pp. 30332–30342,
2020.
[39] T. N. Kipf and M. Welling, “Semi-supervised classiﬁcation with graph
convolutional networks,” 2016, arXiv:1609.02907.
[40] T. Song, S. Liu, W. Zheng, Y. Zong, and Z. Cui, “Instance-adaptive
graph for EEG emotion recognition,” in Proc. AAAI Conf. Artif. Intell.,
vol. 34, no. 3, 2020, pp. 2701–2708.
[41] T. Hamaguchi, H. Oiwa, M. Shimbo, and Y. Matsumoto, “Knowledge
transfer for out-of-knowledge-base entities: a graph neural network
approach,” 2017, arXiv:1706.05674.
[42] L. Zhao et al., “T-GCN: A temporal graph convolutional network for
trafﬁc prediction,” IEEE Trans. Intell. Transp. Syst., vol. 21, no. 9,
pp. 3848–3858, Sep. 2020.
[43] B. Chen, C. L. P. Chen, and T. Zhang, “GDDN: Graph domain
disentanglement network for generalizable EEG emotion recognition,”
IEEE Trans. Affect. Comput., vol. 15, no. 3, pp. 1739–1753, Jul. 2024.
[44] J. Zhu et al., “EEG based depression recognition using improved graph
convolutional neural network,” Comput. Biol. Med., vol. 148, 2022, Art.
no. 105815.
[45] T. Chen, Y. Guo, S. Hao, and R. Hong, “Exploring self-attention
graph pooling with EEG-based topological structure and soft label for
depression detection,” IEEE Trans. Affect. Comput., vol. 13, no. 4,
pp. 2106–2118, Oct./Dec. 2022.
[46] W. Li, H. Wang, and L. Zhuang, “GCNS–Fsmi: EEG recognition of
mental illness based on ﬁne-grained signal features and graph mutual
information maximization,” Expert Syst. Appl., vol. 228, 2023, Art. no.
120227.
[47] Z. Zhang, Q. Meng, L. Jin, H. Wang, and H. Hou, “A novel EEG-
based graph convolution network for depression detection: incorporating
secondary subject partitioning and attention mechanism,” Expert Syst.
Appl., vol. 239, 2024, Art. no. 122356.
[48] Q. Wang et al., “Leveraging brain modularity prior for interpretable
representation learning of fMRI,” IEEE Trans. Biomed. Eng., vol. 71,
no. 8, pp. 2391–2401, Aug. 2024.
[49] W. Cui, M. Sun, Q. Dong, Y. Guo, X.-F. Liao, and Y. Li, “A multi-
view sparse dynamic graph convolution-based region-attention feature
fusion network for major depressive disorder detection,” IEEE Trans.
Computat. Social Syst., vol. 11, no. 2, pp. 2691–2702, Apr. 2024.
[50] Y. Ding, N. Robinson, C. Tong, Q. Zeng, and C. Guan, “LGGNet:
Learning from local-global-graph representations for brain–computer
interface,” IEEE Trans. Neural Netw. Learn. Syst., vol. 35, no. 7,
pp. 9773–9786, Jul. 2024.
[51] X. Gong, C. L. P. Chen, and T. Zhang, “Cross-cultural emotion recog-
nition with eeg and eye movement signals based on multiple stacked
broad learning system,” IEEE Trans. Computat. Social Syst., vol. 11,
no. 2, pp. 2014–2025, Apr. 2024.
[52] T. Wang, R. Mao, S. Liu, E. Cambria, and D. Ming, “Explainable multi-
frequency and multi-region fusion model for affective brain-computer
interfaces,” Inf. Fusion, vol. 118, 2025, Art. no. 102971.
[53] J. A. Hartigan and M. A. Wong, “Algorithm as 136: A k-means
clustering algorithm,” Journal of the royal statistical society,” Ser. Appl.
Statist., vol. 28, no. 1, pp. 100–108, 1979.
[54] A. Kendall, Y. Gal, and R. Cipolla, “Multi-task learning using uncer-
tainty to weigh losses for scene geometry and semantics,” in Proc. IEEE
Conf. Comput. Vis. Pattern Recognit., 2018, pp. 7482–7491.
[55] H. Cai et al., “A multi-modal open dataset for mental-disorder analysis,”
Sci. Data, vol. 9, no. 1, p. 178, 2022.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 


## Page 13

220
IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL SYSTEMS, VOL. 13, NO. 1, FEBRUARY 2026
[56] J. F. Cavanagh, A. Napolitano, C. Wu, and A. Mueen, “The patient
repository for eeg data+ computational tools (pred+ ct),” Front. Neu-
roinf., vol. 11, p. 67, Nov. 2017.
[57] V. J. Lawhern, A. J. Solon, N. R. Waytowich, S. M. Gordon, C. P. Hung,
and B. J. Lance, “EEGNet: a compact convolutional neural network for
EEG-based brain–computer interfaces,” J. Neural Eng., vol. 15, no. 5,
2018, Art. no. 056013.
[58] T. Song et al., “Variational instance-adaptive graph for EEG emotion
recognition,” IEEE Trans. Affect. Comput., vol. 14, no. 1, pp. 343–356,
Jan./Mar. 2021.
[59] S. Sun et al., “Graph theory analysis of functional connectivity in
major depression disorder with high-density resting state EEG data,”
IEEE Trans. Neural Syst. Rehabil. Eng., vol. 27, no. 3, pp. 429–439,
Mar. 2019.
[60] H. Peng et al., “Multivariate pattern analysis of EEG-based functional
connectivity: A study on the identiﬁcation of depression,” IEEE Access,
vol. 7, pp. 92630–92641, 2019.
[61] H. Tian et al., “Neural mechanisms underlying cognitive impairment in
depression and cognitive beneﬁts of exercise intervention,” Behav. Brain
Res., vol. 476, 2025, Art. no. 115218.
[62] J. Zhang et al., “Disrupted brain connectivity networks in drug-naive,
ﬁrst-episode major depressive disorder,” Biol. Psychiatry, vol. 70, no. 4,
pp. 334–342, 2011.
Tong Zhang (Senior Member, IEEE) received the
B.S. degree in software engineering from Sun Yat-
sen University, Guangzhou, China, in 2009, the
M.S. degree in applied mathematics from the Uni-
versity of Macau, Macau, China, in 2011, and
the Ph.D. degree in software engineering from the
University of Macau, Macau, in 2016.
Currently, he is a Professor and Associate Dean
with the School of Computer Science and Engi-
neering, South China University of Technology,
Guangzhou. His research interests include affective
computing, evolutionary computation, neural network, and other machine
learning techniques and their applications.
Dr. Zhang is the Associate Editor of the IEEE TRANSACTIONS
ON
AFFECTIVE COMPUTING, IEEE TRANSACTIONS ON COMPUTATIONAL SOCIAL
SYSTEMS, and Journal of Intelligent Manufacturing. He has been working in
publication matters for many IEEE conferences.
Tingting Hu (Student Member, IEEE) received the
B.S. degree in computer science and technology
in 2022 from the South China University of Tech-
nology, Guangzhou, China, where she is currently
working toward the M.S. degree in computer tech-
nology with the School of Computer Science and
Engineering.
Her research interests include affective computing
and graph neural network.
Mengqi Wu (Student Member, IEEE) received the
B.S. degree in software engineering from Shihezi
University, Shihezi, China, in 2021. She received
the M.S. degree in computer technology in 2024
from the South China University of Technology,
Guangzhou, China, where she is currently working
toward the Ph.D. degree in computer science and
engineering.
Her research interests include affective computing
and graph neural network.
Zihua Xu (Student Member, IEEE) received the
B.S.
degree
in
computer
technology
in
2022
from the South China University of Technology,
Guangzhou, China, where he is currently working
toward the Ph.D. degree in computer science and
technology with Guangdong Provincial Key Labo-
ratory of Computational AI Models and Cognitive
Intelligence, the School of Computer Science and
Engineering.
His research interests include graph neural net-
work and affective computing.
C. L. Philip Chen (Life Fellow, IEEE) received
the M.S. degree in electrical and computer science
from the University of Michigan at Ann Arbor, Ann
Arbor, Michigan, in 1985, and the Ph.D. degree
in electrical and computer science from Purdue
University, West Lafayette, USA, in 1988. He is
currently the Chair Professor and Dean with the
College of Computer Science and Engineering,
South China University of Technology, Guangzhou,
China. His research interests include cybernetics,
systems, and computational intelligence.
Prof. Chen is a Fellow of AAAS, IAPR, CAA, CAAI, and HKIE; a member
of Academia Europaea (AE), and a member of European Academy of Sciences
and Arts (EASA). He received IEEE Norbert Wiener Award in 2018 for his
contribution in systems and cybernetics, and machine learnings, received two
times best transactions paper award from IEEE TRANSACTIONS ON NEURAL
NETWORKS AND LEARNING SYSTEMS for his papers in 2014 and 2018 and he
is a highly cited researcher by Clarivate Analytics from 2018 to 2023. He was
the Editor-in-Chief of the IEEE TRANSACTIONS ON CYBERNETICS, the Editor-
in-Chief of the IEEE TRANSACTIONS ON SYSTEMS, MAN, AND CYBERNETICS:
SYSTEMS, President of IEEE Systems, Man, and Cybernetics Society. He was
a recipient of the 2016 Outstanding Electrical and Computer Engineers Award
from his alma mater, Purdue University (in 1988), after he graduated from
the University of Michigan at Ann Arbor, Ann Arbor, MI, USA, in 1985.
Authorized licensed use limited to: South China Normal University. Downloaded on April 02,2026 at 14:22:08 UTC from IEEE Xplore.  Restrictions apply. 



# Tables

No tables found.

# Images

- Figure 1 (Page 3): `figure_1.png` (294x346)
- Figure 2 (Page 3): `figure_2.png` (294x346)
- Figure 3 (Page 3): `figure_3.png` (119x185)
- Figure 4 (Page 3): `figure_4.png` (180x122)
- Figure 5 (Page 3): `figure_5.png` (133x239)
- Figure 6 (Page 3): `figure_6.png` (294x346)
- Figure 7 (Page 3): `figure_7.png` (143x184)
- Figure 8 (Page 3): `figure_8.png` (140x125)
- Figure 9 (Page 4): `figure_9.png` (184x174)
- Figure 10 (Page 4): `figure_10.png` (289x170)
- Figure 11 (Page 4): `figure_11.png` (289x170)
- Figure 12 (Page 4): `figure_12.png` (289x170)
- Figure 13 (Page 4): `figure_13.png` (146x215)
- Figure 14 (Page 4): `figure_14.png` (137x277)
- Figure 15 (Page 4): `figure_15.png` (137x277)
- Figure 16 (Page 4): `figure_16.png` (115x275)
- Figure 17 (Page 4): `figure_17.png` (137x277)
- Figure 18 (Page 4): `figure_18.png` (137x277)
- Figure 19 (Page 4): `figure_19.png` (113x273)
- Figure 20 (Page 4): `figure_20.png` (184x174)
- Figure 21 (Page 4): `figure_21.png` (184x174)
- Figure 22 (Page 4): `figure_22.png` (184x174)
- Figure 23 (Page 4): `figure_23.png` (137x277)
- Figure 24 (Page 4): `figure_24.png` (105x105)
- Figure 25 (Page 4): `figure_25.png` (137x171)
- Figure 26 (Page 8): `figure_26.png` (1062x370)
- Figure 27 (Page 10): `figure_27.png` (962x561)
- Figure 28 (Page 11): `figure_28.png` (1091x348)
- Figure 29 (Page 11): `figure_29.png` (1091x344)
- Figure 30 (Page 13): `figure_30.png` (320x400)
- Figure 31 (Page 13): `figure_31.png` (320x400)
- Figure 32 (Page 13): `figure_32.png` (320x400)
- Figure 33 (Page 13): `figure_33.png` (320x400)
- Figure 34 (Page 13): `figure_34.png` (320x400)