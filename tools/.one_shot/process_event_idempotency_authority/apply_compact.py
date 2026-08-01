from __future__ import annotations

import base64
import subprocess
import zlib
from pathlib import Path

EXPECTED_BLOBS = {
    "ai_test_asset_center/process_graph_wait_contract.py": "3293315cad87ffedb13e2f90652ae65b2463a0f5",
    "ai_test_asset_center/process_graph_event_transition.py": "2716cf320c45f99ebbdf56db29d76d124bdfbb45",
    "ai_test_asset_center/process_graph_async_transition_observer.py": "7d24e6d73fe5bdbcdc3334c3e05f8d20f5ad6777",
    "tests/test_process_graph_event_transition.py": "c5bff82d25ffcabd70765a4c119f83007ebf189d",
    "tests/test_process_graph_event_run_scope.py": "d8b02fc80deb7d0836472b62815f2630b8290ce9",
}
PATCH_B85 = 'c-rk+ZExE+68`RAK?nz|RLj~pNu9>HE}Bh^-QaeUBFPmv1c4w+bi%7Fc_liD&+vc0;j2hVmYww8KAa0QGBs};&O96rJs1p7^kYOqic1<sWr=AR<Ba0s$3>CHxGckmB3e9#&k=z)c}9yUrom#>>-YO;`X<g#pU_}<>Yt!K{Qm)c`qV?SBA=rGk@+GoD6;vdMg9#JVU$D*5RRe}spoD!`~=p7pfxKAC3(g<`CvDnWiLDwm~*m+-as%GI7&m3gat11^a+fTg0j4rM=ALS4i^!93>Rq><HtNra8dU9-K->=!6XG+R7CUA-%HvuqhyZ5DajI&J%sZJ+(I~$mTZiL(>z(hg3Un|#XZFS&GG_;Sv1FB0!m2X@|k*Lu}p_2{s`9T@PmKK7b={5om}3A=U3O)li$v6FRm`bpD!*yUtInY-kkqB`TY!iL~akUkMqY8lunlqf%NZy`**;XU;s)A3|7Fx45anJ)6nT5uh*AKE<R6w|8jLZxjg?{o09%+B_x5V<`ie~%2Lwz#M~1+L)v#`zAR!K&InEux5%Hz;EkBxm9+5DS+=_O(BLNo--Kt54i1=Sjt-7}a1!?o`v}2(F1WydELm<S1!*4h`Igf5rzl<GF_Z0!0ZBM!TI3L0dHx-y?EJ}rQEo;`N^aTxSh~S+t}$H}8OpPCh0IVv^bsTc0t_G;$$&x6Bu&l82ta}mrL7!ST0r^Ty?9M#h?FENX_Up-<x}vvqdaK}`>>e$hF3m{A4!_9z~Y1ffzT^mlhV={;1^h{#I;|uPlqqL3SaroM>A*5oVE$pV8O=mY+>>%PFv@}CS0Ef$i*+lOmMcG;{rS<nEo@9xm$BHHutZa53HSOUkN!uGMvJ4=F`99n7W2EBN!4O*oqoT_PUBzDRyH>19L2aq|{_v9TqJX%wcT8Qc@veT9fqpGPR7v)?Nev$Y;{s?_~OLFsLW8e3viSGKxsA2y9YCEA|q#DYOXWSxJ|R6yMd}Av;aK6!{YtImu)qV<s$Rn4>^V2{D$S>53ByU_vr#0_FrJ>`csV@C+hU#DLRRXbLPeN-=`yo#HfyVVa{T%Rv~7z=jw<2IzvKILc5NgCB&zh;5OpES8pWBzb<snM4qnxH3h%P8nq`Hbb0<T8PH-hR~9u8Mg`oDnK_BWW-D|L>90`S%UaRRFXMKqk_;CieUEz!&Nv4Wzmv8=InK#-D<1*h#*{FsB14<@PMi7n4tp>SK;G$o(4EjsIj;TRvC=zjgOQ*xn*Q|f*~Y3)!L+5n=#DTS?sv{Tj}!E^*Fc9ta$Y*0HDmYa)VZAw#b|YvsPQ>1Lw2YYR)mOhUH{KwlVd=b~o0r(Gh+#7f33qgfBB$I3;7p<_4&&Ee9C!!=FJGUH$aK#-R382JP7Dis+eFUfQ(=lGdHf=uB(|-P-7VDU`n5ax@q#(rS5(j9Uw<#WuzFGXBVuv0iPuO1ZZ&)~bQcGp>HDu%B<QR&Iteo7omqo1IOdEN5mZHO#V(&9NpyZ<AiP#Wl9^TG1HRYc{aEgrXTIE}UmvJzR_bDsWokdbT~1l`%#fSI(^aQXvQ!wXPI7jig|CJ+?E@H|XaW;4%S<EdU|kdo97HUU|V1+*!DuDc6p~ao2gJ3NKSYJtSaX#drZ5(yG4JifyB+;#HbQN!>5;Bc4Yg9I8r~0GscwbA;dYssiY74cz_jSO68uG%CQWK&k4x7GLPl0y6#FqP!j>aBj&SaIq*z#x(FlTwo5?z_!kBz+;QHa#g}Y%Xw}IBZJ0kU#~l-)mnzRE%r(#yA+gzVhi3~4u*huBzZ+qxL=_+&2v@8{wqojZYoI7(JwaSU82-X6$QxKvxZ?Wxl&p9Q5r$+R0Rxw7cBCH+rDDV{Vf&Df1w7F6JiTu1h|@zF#@$^HO=;TW0Ur57SkPK3O2@g20fFy3vhNxuRDkdwFmc;&|dQ?D_>J5Zq-x+HDRk9+|Ni0F!r%<3j@=^10ZBooT;b@s$QKj#o4R*z(U5xDun%5YdWJ8Y~5O$y=4|+`dj?mxm7eoy%~Ef|E%A=<AwlIY}uhyHg0qUt2=TAp#<=4?KlC%NO#kkAPSsd8t>Sh9J7wy@#(2Q9O{N$6ZAjwufFW(jeDpDo{l2d4a|l&!lWQG>R4dB=O(2$sE_=AV=r#EvM;Udw_Dk@X39#7&EXA9=C9H5i23W}WaJM|Eq{62T*o%}?Ox#<y-6UpAY^on3~Ochm_xWIa@Gjl+5WK|8Rzo_NpTX^J!*G>K30!4nl{htufBD#H2Pl+%g1d}Nm)X2R;QIUzkVReX1s0)Xmm4~4J2FQt$Mjr#$B_y>rU(Ys?~0O8af(X$tpzhf;^BcO4Y56xP#F<y)_z^3ml97gY<)E1RI%c5oRPQhcWq1FOeQm5#;s~GMk~n;DONn?!S0v%(zVo^J$3-r~`t<YQKB1E(tx}-bE+L;V~X1CqZyD{cv#l{wPAjgM-oWac{8iJzb}kz3=QgrSOZP(U4yZ!JpxK00$1-lBmI5x;T5nDdblxh}%Q8wKN(-yj2;_p&ZbMkS49bvKW(v+_f6KUX#i5n;~xHg<#ix$4Y;Hr~WQ6UXy>PDGd~_9sJ&6SEtN<jokBKy03kE6)&x(HRDGHW}-K3c!a_LzAm{@{CK=Ol)A<6{>edWaJ?36>Q~o$V@wO9gqFjT;V7OR9R|THI-CyU+3BmHrA_$Wh|<Q)tFrg+`8_K9IaN3kPNCHO#+)m>FV`l)G$N_I9Yx!rL;PSsZ!mTdD}POp;FJKIt(paFV{6lbs$hFfU98GmGn{zi3T<y(T~E#@7hi7Wp)veAxxTr$y7WyJ8qp=|5#8LL-Tw6^{Q0-5^S?|!`@*LqUb!8Opwd0r;neotm&no`r+3pHaSGfap+;m&=)?<K&X$9W*|+Kj@A}0<4Qm>UXp^_qShW%>EwR2NYV_2;6U`gCyPVS9)_Vh1Z_)lrw6<1WrewDf7NiP}&Uu^g%TF_h9A`#H?1x7(CGZQ5mlQX!Noo^tFODkg43T2W4wxn6DwN%EC~}bBv^ZbRZf;mx?CZsq_*smzF~Nwrie0$N`!+c!GtqeI>Nn*wrJg0ixD60^#A@a52DM7auH*f`7r@T1E`jQo$!A{Ww|Sl?RU6!mKJbJ6=+uO}9c$Z2MAnzJ1FsfyR<97hfZN#iZ;0^fz`(Ba6%wGGNbrA@`rk$=)lV^rOMXqnuM0bZrLD=QrSXw65Pw`7xBdx>WlF0'
TEST_B85 = 'c-qZcUvJwu5`WL95QKej04Gg~eKIZ!<WgS|Y&YpPxdI2nAY_Slc#$P7Nu}}S{M~OjBqd6sWF^;PHxG_X&Tz>2^Bc~{Yo2cdLe`}$IVB`u+al*Oh_Wn~kz{!$j*ixfCeDkUolPh$)ORx#F(RpuBocy364OjlZb=KC$5aTi;ZgBG=o3s5$)ij#wRjMTK#%b<lWa?0yWqEo0YRR@lCgT=R4r*n`M`3g&2=KN%VK9^Bwq>2pK7s_qX7QqU#~yRZ?5L#*ZIGuIAZ8J3yA5_@o4FdQ?w<{Z|=$cm&=>G>-+258*+W~_43p82QB^X{__5xcjWJ%Zm)iwH^;7SfBSs>sWW)<b^hg}9gXvC!BR>Fb|V^;iJmmZBAiCaP7}~)8kf?ylv2deQ9{=NVSK{rAEna9Q<Ro;76{3M{{}aCMuUqW{F|L+$uHq?aPmu#uvpGefXGW8E2<a#s{TTjgtH*b3nI$ZmdS8hPi?7u$Z`7f?cKdQQ$+GXjlauzLb-6K#7;=Mg=4L8U3Y$upKKj84iGRAK%n3OX4N`+tBcT*(&uPfq%>Tr-NU9S7+BIQ-jPST14)e~wIEI*9y9l>c|o}jNZ?ph<F&kLW9+5zt*I@rtthuNoOWqywSAo5`v@xS!Liwz#d=HcUQk(Jx4n<4sn-T)i+n%+6o?z*|2u>V%o7{x;$8oeZ#u!!c#iRY42en0t-FTSk)$w4$B;g-i=VCsMJ1e-Gr4QHxCuhdYQK$S&hmtIeavGCMQB9m_#@~9UiC>@j3ll6yb424cvsYbP&v1h`pfxY`*kA!Fl>x~kNOCuEJ{gMEK69nA)=shSE2oit=5U7EYHBrQnF?=O^gC<q<JJ)oc|V}KfBYPP7ko`JEP0jMy2Bm23?37BnNzAEQ`-K2EgoX&M_3+KM*5X=K2im<xMUFv7VGMNEsiTU0&nA=yYQ}PDXq~<p}E6IGZyQ(quCZmK32mIX>$BAozKWkIC~D`Z7l)Z*X}|Q|-QTS5U?H+x+hC^5fh``)7KBIS+rs8NZJ=RU7z@ZsqkVnHPDQZxCd)T0qq;J@uU3U8Z?u9^<9Rp*4$-e<3fm_aWkg+x{!k4?^35(?rdyczJ=O5y?`3s?8nc{n^=a@7oNPV_;hrs~fgIPC}YRD=2D!;GUOs+C%gV`CCHivyGlf0uj6;TP6GatcL-lri7_74=(hEwue+L8A(t_fnM{zhfEa+4D>+Y_^@G9oL1F9-|Db~Ez4jJVEF{AQgj^cN(qhANUf=_g>WziHs5(n;L~rrknh*AU6?!qpegxVxQj*`+7rh$y`b9&%1pdEff2x?(jCdOxR+DArgQCKpja_T4Utg;(@&>%Y?w!Zcb5wxy@xN(c?JCrW3<_Dx&hZ75b3g-lrRDf8*ETqf;x+cM{sjNgbT+=uKeCPAF$pk$yZSM!_+}ma6fRqGLr*d+K8`1#I54lRY~1D2uCtIrf3)CJu8^l8O+JUc^Q|np|H;R7Q$02*rtzxyok(c4rrL88MVjHMwrB(B0Xu+6Y!nPSyi;AD>Pwzh9zy?%2EG1m98jL33Fq*PLoQeEt~5-t>{Da#2~#ee!+N<b8IXbf@Azty*4h5$c4!os{eA)X^$Bqw-~o<C^=l()1tN~79+5CH4SR7!-2!qy@i-rU>~PP9)KALwPy4xx-TD2etz##^!J#oxXOm6aW}!kYBR#A@^4}mq)f<#8d!EsSwQN+Zk(HvRSG*hgA3CtPcZF<8iHrI;C|m=7QhYDe6p}3(`s(1%?RlKSd*Qd!qWER>|FnQZ%OgG1GE`^mbLeQ-0B&kO??ko7T{qQrq!{mC{%kd|F)>AS#%ixy~%MlW90r0XuTaAyTH%dTB;Df(EG0kRV2VkE}BRZt|CE6$x~(a2Pq3Nd)KToPffG&RJFERU|?$sfu^Zah~q&G6ov6G^WRU-LkAC>N4(;Ri<j!IrxxXTimqFfE4X@vdxu`;k2C{%XGryrF{NLm(RP(YGi|-fL!6)&DFrQQvVhDGVCvaz4oXnHlIB9_KNT=&hsR#Lg`hR3^gmRP3fJx7Jv}nrxvM+9Xhm?i3I(+{U<kvoaj_<>k=4$YG|NR#S+O|ky=B!o;;8N~g1_Z!5QxN!M(EobvSI@)bKns<7wy_YJAI?P=x1t{2%5ES*m?NumaG<#!*Mzfi=D;9hgV+Ya`e<{w9mTv+|if!9Cn53Rni(ZNqNEH9v?RN2Hu2d3_Vtxn_xi^gki(&Hfg?sd~bE|je%=HY24aF2_YWi3pgTTP_RinE%*B&>9q5P<^mIW6+z>2t9c#eqXs<*^y7qTmcI8}F7u?DK5Of?m6w62Jbj|ctnF2Z@O5@isEk-@jN34iIueV)_aP=S$HaQMZNwib7X*qY6}kNYq4JG+OOyo@#8Rc8>nnw`FE91>_WSU7=78dx4C^=UVFTl*!*N^#{HqM@wbysC*^u2jw)2R=NGAzrYe`lBB98#W6pTYPvZ|M@Be7lSz7N^~xX+f244~#00~qd5AA;?EyhXUp<%30OT<)~Ui%ICkvZ=nByQ2M=XETcR|5JBb--2tUj1;If(krieZQqoohL3Bryd|f?;`TQSJ<HiBcXjUDC<cI&dxnnWB%MQ|I$km$a?or><{L6rwf;Y%RM6=V=C&PF2UqC1Xkot$M5}OPo6Nq06p*Upv`k<ItOo%K!);W++&3~XIgKeRRM1uRO!LlNAE#Ngo7@U#lqY$bC}dVRef#M{*g{d4GA=pCMy<UeA9vnaw38+7ucs0<zsc}3m@XJ`Im6GFcXy$e-ZXC$096Khta=M&W-eRjem1y<07&ZlhHL7P{0-Q}Me29U%cgs^uUs9p^VRVlF&!uDy|UHH9Cg`qf7g?MOZEQ5r#@4wN>eQ@4nc;kte5s1eHg?4tu{;8>gvAudf|h=Su*Fu)|7c(_Ptu$J6d*rk5!#EQJ4hkR{hrH!u{P7pp~P8I_(){*K<GGEBYjjcMNxhalY|C!1#av'


def blob(path: str) -> str:
    return subprocess.check_output(["git", "hash-object", path], text=True).strip()


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"replacement_scope_invalid:{path}:{text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def decode(value: str) -> bytes:
    return zlib.decompress(base64.b85decode(value.encode()))


def main() -> None:
    drift = {path: (expected, blob(path)) for path, expected in EXPECTED_BLOBS.items() if blob(path) != expected}
    if drift:
        raise RuntimeError(f"source_blob_drift:{drift}")
    patch = Path("/tmp/process_event_idempotency_authority.patch")
    patch.write_bytes(decode(PATCH_B85))
    subprocess.run(["git", "apply", "--check", str(patch)], check=True)
    subprocess.run(["git", "apply", str(patch)], check=True)
    replace_once(
        "tests/test_process_graph_event_transition.py",
        '{"id": "op_submit", "method": "POST", "path": "/orders", "system_ref": "orders"}',
        '{"id": "op_submit", "method": "POST", "path": "/orders", "system_ref": "orders", "request_example": {"idempotency_key": "<request_id>"}}',
    )
    replace_once(
        "tests/test_process_graph_event_run_scope.py",
        '                "path": "/orders",\n                "system_ref": "orders",\n            },',
        '                "path": "/orders",\n                "system_ref": "orders",\n                "request_example": {\n                    "idempotency_key": "<request_id>"\n                },\n            },',
    )
    Path("tests/test_process_graph_event_idempotency_source_authority.py").write_bytes(decode(TEST_B85))


if __name__ == "__main__":
    main()
