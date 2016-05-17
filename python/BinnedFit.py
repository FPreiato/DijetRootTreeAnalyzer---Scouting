from optparse import OptionParser
import ROOT as rt
import rootTools
from framework import Config
from array import *
from itertools import *
from operator import *
from WriteDataCard import initializeWorkspace,convertToTh1xHist,applyTurnon
import os
import random
import sys
import math

densityCorr = False

def binnedFit(pdf, data, fitRange='Full',useWeight=False):

    if useWeight:
        fr = pdf.fitTo(data,rt.RooFit.Range(fitRange),rt.RooFit.Extended(True),rt.RooFit.SumW2Error(True),rt.RooFit.Save(),rt.RooFit.Minimizer('Minuit2','migrad'),rt.RooFit.Strategy(2))
        migrad_status = fr.status()
        hesse_status = -1        
    else:
        nll = pdf.createNLL(data,rt.RooFit.Range(fitRange),rt.RooFit.Extended(True),rt.RooFit.Offset(True))
        m2 = rt.RooMinimizer(nll)
        m2.setStrategy(2)
        m2.setMaxFunctionCalls(100000)
        m2.setMaxIterations(100000)
        migrad_status = m2.minimize('Minuit2','migrad')
        improve_status = m2.minimize('Minuit2','improve')
        hesse_status = m2.minimize('Minuit2','hesse')
        minos_status = m2.minos()
        fr = m2.save()
        
    return fr

def effFit(pdf, data, conditionalObs):    
    nll = pdf.createNLL(data,rt.RooFit.Range('Eff'),rt.RooFit.Offset(True),rt.RooFit.ConditionalObservables(conditionalObs))
    m2 = rt.RooMinimizer(nll)
    m2.setStrategy(2)
    m2.setMaxFunctionCalls(100000)
    m2.setMaxIterations(100000)
    migrad_status = m2.minimize('Minuit2','migrad')
    improve_status = m2.minimize('Minuit2','improve')
    hesse_status = m2.minimize('Minuit2','hesse')
    minos_status = m2.minos()
    fr = m2.save()
    return fr



def simFit(pdf, data, fitRange, effPdf, effData, conditionalObs):
    
    effNll = effPdf.createNLL(effData,rt.RooFit.Range('Eff'),rt.RooFit.Offset(True),rt.RooFit.ConditionalObservables(conditionalObs))

    fitNll = pdf.createNLL(data,rt.RooFit.Range(fitRange),rt.RooFit.Extended(True),rt.RooFit.Offset(True))

    simNll = rt.RooAddition("simNll", "simNll", rt.RooArgList(fitNll, effNll))
        
    m2 = rt.RooMinimizer(simNll)
    m2.setStrategy(2)
    m2.setMaxFunctionCalls(100000)
    m2.setMaxIterations(100000)
    migrad_status = m2.minimize('Minuit2','migrad')
    improve_status = m2.minimize('Minuit2','improve')
    hesse_status = m2.minimize('Minuit2','hesse')
    minos_status = m2.minos()
    fr = m2.save()

    return fr

def convertSideband(name,w,x):
    if name=="Full":
        return "Full"
    names = name.split(',')
    nBins = (len(x)-1)
    iBinX = -1
    sidebandBins = []
    for ix in range(1,len(x)):
        iBinX+=1
        w.var('mjj').setVal((x[ix]+x[ix-1])/2.)
        inSideband = 0
        for fitname in names:
            inSideband += ( w.var('mjj').inRange(fitname) )
        if inSideband: sidebandBins.append(iBinX)

    sidebandGroups = []
    for k, g in groupby(enumerate(sidebandBins), lambda (i,x):i-x):
        consecutiveBins = map(itemgetter(1), g)
        sidebandGroups.append([consecutiveBins[0],consecutiveBins[-1]+1])
        
    newsidebands = ''
    nameNoComma = name.replace(',','')
        
    for iSideband, sidebandGroup in enumerate(sidebandGroups):
        if not w.var('th1x').hasRange('%s%i'%(nameNoComma,iSideband)):
            w.var('th1x').setRange("%s%i"%(nameNoComma,iSideband),sidebandGroup[0],sidebandGroup[1])
        newsidebands+='%s%i,'%(nameNoComma,iSideband)
    newsidebands = newsidebands[:-1]
    return newsidebands

def convertFunctionToHisto(background_,name_,N_massBins_,massBins_):

    background_hist_ = rt.TH1D(name_,name_,N_massBins_,massBins_)

    for bin in range (0,N_massBins_):
        xbinLow = massBins_[bin]
        xbinHigh = massBins_[bin+1]
        binWidth_current = xbinHigh - xbinLow
        value = background_.Integral(xbinLow , xbinHigh) / binWidth_current
        background_hist_.SetBinContent(bin+1,value)

    return background_hist_

def calculateChi2AndFillResiduals(data_obs_TGraph_,background_hist_,hist_fit_residual_vsMass_,workspace_,prinToScreen_=0):
    
    N_massBins_ = data_obs_TGraph_.GetN()
    MinNumEvents = 10
    nParFit = 4
    if workspace_.var('meff').getVal()>0 and workspace_.var('seff').getVal()>0 :
        nParFit = 6

    chi2_FullRangeAll = 0
    chi2_PlotRangeAll = 0
    chi2_PlotRangeNonZero = 0
    chi2_PlotRangeMinNumEvents = 0 

    N_FullRangeAll = 0
    N_PlotRangeAll = 0
    N_PlotRangeNonZero = 0
    N_PlotRangeMinNumEvents = 0 

    for bin in range (0,N_massBins_):
        ## Values and errors
        value_data = data_obs_TGraph_.GetY()[bin]
        err_low_data = data_obs_TGraph_.GetEYlow()[bin]
        err_high_data = data_obs_TGraph_.GetEYhigh()[bin]
        xbinCenter = data_obs_TGraph_.GetX()[bin] 
        xbinLow = data_obs_TGraph_.GetX()[bin]-data_obs_TGraph_.GetEXlow()[bin] 
        xbinHigh = data_obs_TGraph_.GetX()[bin]+data_obs_TGraph_.GetEXhigh()[bin]
        binWidth_current = xbinHigh - xbinLow
        #value_fit = background_.Integral(xbinLow , xbinHigh) / binWidth_current
        value_fit = background_hist_.GetBinContent(bin+1)
        
        ## Fit residuals

        err_tot_data = 0
        if (value_fit >= value_data):
            err_tot_data = err_high_data  
        else:
            err_tot_data = err_low_data
        plotRegions = plotRegion.split(',')
        checkInRegions = [xbinCenter>workspace_.var('mjj').getMin(reg) and xbinCenter<workspace_.var('mjj').getMax(reg) for reg in plotRegions]
        if any(checkInRegions):
            fit_residual = (value_data - value_fit) / err_tot_data
            err_fit_residual = 1
        else:
            fit_residual = 0
            err_fit_residual = 0

        ## Fill histo with residuals

        hist_fit_residual_vsMass_.SetBinContent(bin+1,fit_residual)
        hist_fit_residual_vsMass_.SetBinError(bin+1,err_fit_residual)

        ## Chi2

        chi2_FullRangeAll += pow(fit_residual,2)
        N_FullRangeAll += 1        
        plotRegions = plotRegion.split(',')
        checkInRegions = [xbinCenter>workspace_.var('mjj').getMin(reg) and xbinCenter<workspace_.var('mjj').getMax(reg) for reg in plotRegions]
        if any(checkInRegions):
            #print '%i: obs %.0f, exp %.2f, chi2 %.2f'%(bin, value_data* binWidth_current * lumi, value_fit* binWidth_current * lumi, pow(fit_residual,2))
            chi2_PlotRangeAll += pow(fit_residual,2)
            N_PlotRangeAll += 1
            if (value_data > 0):
                chi2_PlotRangeNonZero += pow(fit_residual,2)
                N_PlotRangeNonZero += 1
                if(value_data * binWidth_current * lumi > MinNumEvents):
                    chi2_PlotRangeMinNumEvents += pow(fit_residual,2)
                    N_PlotRangeMinNumEvents += 1
    
    #==================
    # Calculate chi2/ndf
    #==================

    # ndf
    ndf_FullRangeAll = N_FullRangeAll - nParFit    
    ndf_PlotRangeAll = N_PlotRangeAll - nParFit    
    ndf_PlotRangeNonZero = N_PlotRangeNonZero - nParFit    
    ndf_PlotRangeMinNumEvents = N_PlotRangeMinNumEvents - nParFit    

    chi2_ndf_FullRangeAll = chi2_FullRangeAll / ndf_FullRangeAll
    chi2_ndf_PlotRangeAll = chi2_PlotRangeAll / ndf_PlotRangeAll
    chi2_ndf_PlotRangeNonZero = chi2_PlotRangeNonZero / ndf_PlotRangeNonZero
    chi2_ndf_PlotRangeMinNumEvents = chi2_PlotRangeMinNumEvents / ndf_PlotRangeMinNumEvents

    return [chi2_FullRangeAll, ndf_FullRangeAll, chi2_PlotRangeAll, ndf_PlotRangeAll, chi2_PlotRangeNonZero, ndf_PlotRangeNonZero, chi2_PlotRangeMinNumEvents, ndf_PlotRangeMinNumEvents]


if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option('-c','--config',dest="config",type="string",default="config/run2.config",
                  help="Name of the config file to use")
    parser.add_option('-d','--dir',dest="outDir",default="./",type="string",
                  help="Output directory to store cards")
    parser.add_option('-l','--lumi',dest="lumi", default=1.,type="float",
                  help="integrated luminosity in pb^-1")
    parser.add_option('-b','--box',dest="box", default="CaloDijet",type="string",
                  help="box name")
    parser.add_option('--no-fit',dest="noFit",default=False,action='store_true',
                  help="Turn off fit (useful for visualizing initial parameters)")
    parser.add_option('--fit-region',dest="fitRegion",default="Full",type="string",
                  help="Fit region")
    parser.add_option('--plot-region',dest="plotRegion",default="Full",type="string",
                  help="Plot region")
    parser.add_option('-i','--input-fit-file',dest="inputFitFile", default=None,type="string",
                  help="input fit file")
    parser.add_option('-w','--weight',dest="useWeight",default=False,action='store_true',
                  help="use weight")
    parser.add_option('-s','--signal',dest="signalFileName", default=None,type="string",
                  help="input dataset file for signal pdf")
    parser.add_option('-m','--model',dest="model", default="gg",type="string",
                  help="signal model")
    parser.add_option('--mass',dest="mass", default=750,type="float",
                  help="mgluino")
    parser.add_option('--xsec',dest="xsec", default=1,type="float",
                  help="cross section in pb")
    parser.add_option('-t','--trigger',dest="triggerDataFile", default=None,type="string",
                  help="trigger data file")
    parser.add_option('--sim',dest="doSimultaneousFit", default=False,action='store_true',
                  help="do simultaneous trigger fit")


    (options,args) = parser.parse_args()
    
    cfg = Config.Config(options.config)
    
    box = options.box
    lumi = options.lumi
    noFit = options.noFit
    fitRegion = options.fitRegion
    plotRegion = options.plotRegion
    
    myTH1 = None
    for f in args:
        if f.lower().endswith('.root'):
            rootFile = rt.TFile(f)
            myTH1 = rootFile.Get('h_mjj_HLTpass_HT250_1GeVbin')
    if myTH1 is None:
        print "give a root file as input"

    w = rt.RooWorkspace("w"+box)

    paramNames, bkgs = initializeWorkspace(w,cfg,box)

        
    if options.inputFitFile is not None:
        inputRootFile = rt.TFile.Open(options.inputFitFile,"r")
        wIn = inputRootFile.Get("w"+box).Clone("wIn"+box)            
        if wIn.obj("fitresult_extDijetPdf_data_obs") != None:
            frIn = wIn.obj("fitresult_extDijetPdf_data_obs")
        elif wIn.obj("nll_extDijetPdf_data_obs") != None:
            frIn = wIn.obj("nll_extDijetPdf_data_obs")
        elif wIn.obj("fitresult_extDijetPdf_data_obs_with_constr") != None:
            frIn = wIn.obj("fitresult_extDijetPdf_data_obs_with_constr")
        elif wIn.obj("nll_extDijetPdf_data_obs_with_constr") != None:
            frIn = wIn.obj("nll_extDijetPdf_data_obs_with_constr")
                        
        print "restoring parameters from fit"
        frIn.Print("V")
        for p in rootTools.RootIterator.RootIterator(frIn.floatParsFinal()):
            w.var(p.GetName()).setVal(p.getVal())
            w.var(p.GetName()).setError(p.getError())

    
    x = array('d', cfg.getBinning(box)[0]) # mjj binning
    
    th1x = w.var('th1x')
    nBins = (len(x)-1)
    th1x.setBins(nBins)

    # get trigger dataset    
    triggerData = None
    if options.triggerDataFile is not None:
        triggerFile = rt.TFile.Open(options.triggerDataFile)
        names = [k.GetName() for k in triggerFile.GetListOfKeys()]
        if 'triggerData' not in names:
            tree = triggerFile.Get("rootTupleTree/tree")      
            triggerData = rt.RooDataSet("triggerData","triggerData",rt.RooArgSet(w.var("mjj"),w.cat("cut")))        
            cutString = 'abs(deltaETAjj)<1.3&&abs(etaWJ_j1)<2.5&&abs(etaWJ_j2)<2.5&&pTWJ_j1>60&&pTWJ_j2>30&&PassJSON&&(passHLT_CaloJet40_CaloScouting_PFScouting||passHLT_L1HTT_CaloScouting_PFScouting)&&mjj>=%i&&mjj<%i'%(w.var("mjj").getMin("Eff"),w.var("mjj").getMax("Eff"))
            #set the RooArgSet and save       
            tree.Draw('>>elist',cutString,'entrylist')        
            elist = rt.gDirectory.Get('elist')    
            entry = -1
            while True:
                entry = elist.Next()
                if entry == -1: break
                tree.GetEntry(entry)          
                a = rt.RooArgSet(w.var('mjj'),w.cat('cut'))   
                a.setRealValue('mjj',tree.mjj)                    
                a.setCatIndex('cut',int(tree.passHLT_CaloScoutingHT250))
                triggerData.add(a)            
            triggerOutFile = rt.TFile.Open(options.outDir+"/triggerData.root","recreate")
            triggerOutFile.cd()
            triggerData.Write()
            triggerOutFile.Close()
        else:            
            triggerData = triggerFile.Get("triggerData")        
        rootTools.Utils.importToWS(w,triggerData)

    
    sideband = convertSideband(fitRegion,w,x)
    plotband = convertSideband(plotRegion,w,x)

    extDijetPdf = w.pdf('extDijetPdf')

    myTH1.Rebin(len(x)-1,'data_obs_rebin',x)
    myRebinnedTH1 = rt.gDirectory.Get('data_obs_rebin')
    myRebinnedTH1.SetDirectory(0)
    
    myRealTH1 = convertToTh1xHist(myRebinnedTH1)        
    
    dataHist = rt.RooDataHist("data_obs","data_obs",rt.RooArgList(th1x), rt.RooFit.Import(myRealTH1))
    dataHist.Print('v')
    
    
    rootTools.Utils.importToWS(w,dataHist)

    if noFit and options.inputFitFile is not None:
        fr = frIn
        fr.Print('v')    
        rootTools.Utils.importToWS(w,fr)
    elif noFit:
        fr = rt.RooFitResult()
        effFr = rt.RooFitResult()
    else:        
        if not options.doSimultaneousFit:
            if options.triggerDataFile is not None:
                effFr = effFit(w.pdf('effPdf'),w.data('triggerData'),rt.RooArgSet(w.var('mjj')))
                #hessePdf = effFr.createHessePdf(rt.RooArgSet(w.var('meff'),w.var('seff')))
                #rootTools.Utils.importToWS(w,effFr)
                #rootTools.Utils.importToWS(w,hessePdf)                
                #w.factory('PROD::extDijetPdfHesse(extDijetPdf,%s)'%(hessePdf.GetName()))
                #extDijetPdf = w.pdf('extDijetPdfHesse')
                w.var('meff_Mean').setVal(w.var('meff').getVal())
                w.var('seff_Mean').setVal(w.var('seff').getVal())
                w.var('meff_Sigma').setVal(w.var('meff').getError())
                w.var('seff_Sigma').setVal(w.var('seff').getError())
                                
            fr = binnedFit(extDijetPdf,dataHist,sideband,options.useWeight)
            if options.triggerDataFile is not None:
                effFr.Print('v')
                effFr.covarianceMatrix().Print('v')
                effFr.correlationMatrix().Print('v')
                corrHistEff = effFr.correlationHist('correlation_matrix_eff')                
                rootTools.Utils.importToWS(w,effFr)
            fr.Print('v')
            fr.covarianceMatrix().Print('v')
            fr.correlationMatrix().Print('v')
            corrHist = fr.correlationHist('correlation_matrix')
            rt.gStyle.SetOptStat(0)
            corrCanvas = rt.TCanvas('c','c',500,500)
            corrCanvas.SetRightMargin(0.15)
            corrHist.Draw('colztext')
            corrCanvas.Print(options.outDir+'/corrHist.pdf')
            corrCanvas.Print(options.outDir+'/corrHist.C')
            if options.triggerDataFile is not None:
                corrHistEff.Draw('colztext')
                corrCanvas.Print(options.outDir+'/corrHistEff.pdf')
                corrCanvas.Print(options.outDir+'/corrHistEff.C')
        else:            
            fr = simFit(extDijetPdf,dataHist,sideband,w.pdf('effPdf'),w.data('triggerData'),rt.RooArgSet(w.var('mjj')))
            fr.Print('v')    
            fr.Print('v')
            fr.covarianceMatrix().Print('v')
            fr.correlationMatrix().Print('v')
            corrHist = fr.correlationHist('correlation_matrix')
            rt.gStyle.SetOptStat(0)
            corrCanvas = rt.TCanvas('c','c',500,500)
            corrCanvas.SetRightMargin(0.15)
            corrHist.Draw('colztext')
            corrCanvas.Print(options.outDir+'/corrHistSim.pdf')
            corrCanvas.Print(options.outDir+'/corrHistSim.C')
        
        total = extDijetPdf.expectedEvents(rt.RooArgSet(th1x))
            
        rootTools.Utils.importToWS(w,fr)

    
    # get signal histo if any
    signalHistos = []
    signalHistosOriginal = []
    signalHistosRebin = []
    if options.signalFileName is not None:
        signalFile = rt.TFile.Open(options.signalFileName)
        names = [k.GetName() for k in signalFile.GetListOfKeys()]
        for name in names:
            d = signalFile.Get(name)
            if isinstance(d, rt.TH1):
                if name=='h_%s_%i'%(options.model,options.mass):
                    d.Scale(options.xsec*lumi/d.Integral())
                    if options.triggerDataFile is not None:
                        d_turnon = applyTurnon(d,effFr,w)
                        name+='_turnon'
                        d = d_turnon
                    d.Rebin(len(x)-1,name+'_rebin',x)
                    d_rebin = rt.gDirectory.Get(name+'_rebin')
                    d_rebin.SetDirectory(0)
    
                    signalHistosOriginal.append(d)
                    signalHistosRebin.append(d_rebin)
    
                    d_th1x = convertToTh1xHist(d_rebin)
                    signalHistos.append(d_th1x)
        

    asimov = extDijetPdf.generateBinned(rt.RooArgSet(th1x),rt.RooFit.Name('central'),rt.RooFit.Asimov())
        
    opt = [rt.RooFit.CutRange(myRange) for myRange in plotband.split(',')]
    asimov_reduce = asimov.reduce(opt[0])
    dataHist_reduce = dataHist.reduce(opt[0])
    for iOpt in range(1,len(opt)):
        asimov_reduce.add(asimov.reduce(opt[iOpt]))
        dataHist_reduce.add(dataHist.reduce(opt[iOpt]))

        
    for i in range(0,len(x)-1):
        th1x.setVal(i+0.5)
        predYield = asimov.weight(rt.RooArgSet(th1x))
        dataYield = dataHist_reduce.weight(rt.RooArgSet(th1x))
        print "%i <= mjj < %i; prediction: %.2f; data %i"  % (x[i],x[i+1],predYield,dataYield)
        
    rt.TH1D.SetDefaultSumw2()
    
    # start writing output
    rt.gStyle.SetOptStat(0)
    rt.gStyle.SetOptTitle(0)
    c = rt.TCanvas('c','c',600,700)
    rootFile = rt.TFile.Open(options.outDir + '/' + 'Plots_%s'%box + '.root','recreate')
    tdirectory = rootFile.GetDirectory(options.outDir)
    if tdirectory==None:
        print "making directory"
        rootFile.mkdir(options.outDir)
        tdirectory = rootFile.GetDirectory(options.outDir)
        tdirectory.Print('v')
        
    h_th1x = asimov_reduce.createHistogram('h_th1x',th1x)
    h_data_th1x = dataHist_reduce.createHistogram('h_data_th1x',th1x)
    
    boxLabel = "%s %s Fit" % (box,fitRegion)
    plotLabel = "%s Projection" % (plotRegion)

    if options.triggerDataFile is not None:
        
        d = rt.TCanvas('d','d',500,400)
        #binning = rt.RooBinning(int(w.var('mjj').getMax('Eff')-w.var('mjj').getMin('Eff')),w.var('mjj').getMin('Eff'),w.var('mjj').getMax('Eff'))
        binning = rt.RooBinning(w.var('mjj').getMin('Eff'),w.var('mjj').getMax('Eff'))
        xEff = array('d',[w.var('mjj').getMin('Eff')])
        for iBin in x[1:-1]:
        #for iBin in range(int(x[0])+1,int(x[-1]),1):
            if iBin<w.var('mjj').getMax('Eff'):
                binning.addBoundary(iBin)
                xEff.append(iBin)
        xEff.append(w.var('mjj').getMax('Eff'))
        h_numerator = rt.TH1D('numerator','numerator',len(xEff)-1,xEff)
        h_denominator = rt.TH1D('denominator','denominator',len(xEff)-1,xEff)
        
        w.data('triggerData').fillHistogram(h_numerator,rt.RooArgList(w.var('mjj')),'cut==1')
        w.data('triggerData').fillHistogram(h_denominator,rt.RooArgList(w.var('mjj')))
        effGraph = rt.TGraphAsymmErrors(h_numerator,h_denominator)
        histo = effGraph.GetHistogram()
        histo.GetXaxis().SetRangeUser(w.var('mjj').getMin('Eff'),w.var('mjj').getMax('Eff'))
        histo.SetMinimum(0.)
        histo.SetMaximum(1.1)        
        #frame = w.var('mjj').frame(rt.RooFit.Title(""))
        #w.data('triggerData').plotOn(frame,rt.RooFit.Efficiency(w.cat('cut')),rt.RooFit.Binning(binning),rt.RooFit.DrawOption("pe0"))
        #w.function('effFunc').plotOn(frame,rt.RooFit.LineColor(rt.kRed))
        #frame.SetMaximum(1.1)        
        #frame.SetMinimum(0.)        
        #frame.GetXaxis().SetRangeUser(w.var('mjj').getMin('Eff'),w.var('mjj').getMax('Eff'))
        #frame.Draw()
        histo.Draw()
        effGraph.SetMarkerStyle(20)
        effGraph.SetMarkerColor(rt.kBlack)
        effGraph.SetLineColor(rt.kBlack)
        effGraph.Draw('apezsame')
        effTF1 = w.function('effFunc').asTF(rt.RooArgList(w.var('mjj')))
        effTF1.Draw("lsame")
        

        histo.GetYaxis().SetTitle('Efficiency')
        histo.GetXaxis().SetTitle('m_{jj} [GeV]')
        histo.GetXaxis().SetTitleSize(0.045)
        histo.GetYaxis().SetTitleSize(0.045)
        l = rt.TLatex()
        l.SetTextAlign(11)
        l.SetTextSize(0.04)
        l.SetTextFont(42)
        l.SetNDC()
        l.DrawLatex(0.65,0.92,"%i pb^{-1} (%i TeV)"%(lumi,w.var('sqrts').getVal()/1000.))
        l.SetTextFont(62)
        l.SetTextSize(0.05)
        l.DrawLatex(0.1,0.92,"CMS")
        l.SetTextFont(52)
        l.SetTextSize(0.04)
        l.DrawLatex(0.2,0.92,"Preliminary")
        leg = rt.TLegend(0.6,0.4,0.79,0.58)
        leg.SetTextFont(42)
        leg.SetFillColor(rt.kWhite)
        leg.SetFillStyle(0)
        leg.SetLineWidth(0)
        leg.SetLineColor(rt.kWhite)
        leg.AddEntry(effGraph,"Data","pe")
        leg.AddEntry(effTF1,"Fit","l")
        leg.Draw()
        
        pave_param = rt.TPaveText(0.45,0.15,0.9,0.25,"NDC")
        pave_param.SetTextFont(42)
        pave_param.SetFillColor(0)
        pave_param.SetBorderSize(0)
        pave_param.SetFillStyle(0)
        pave_param.SetTextAlign(11)
        pave_param.SetTextSize(0.045)
        if w.var('meff').getVal()>0 and w.var('seff').getVal()>0:
            pave_param.AddText("m_{eff}"+" = {0:.2f} #pm {1:.2f}".format(w.var('meff').getVal(), (w.var('meff').getErrorHi() - w.var('meff').getErrorLo())/2.0,))
            pave_param.AddText("#sigma_{eff}"+" = {0:.2f} #pm {1:.2f}".format(w.var('seff').getVal(), (w.var('seff').getErrorHi() - w.var('seff').getErrorLo())/2.0))
        pave_param.Draw("SAME")        
        d.Print(options.outDir+"/eff_mjj_%s_%s.pdf"%(fitRegion.replace(',','_'),box))
        d.Print(options.outDir+"/eff_mjj_%s_%s.C"%(fitRegion.replace(',','_'),box))
        tdirectory.cd()
        d.Write()

    
    background_pdf = w.pdf('%s_bkg_unbin'%box)
    background= background_pdf.asTF(rt.RooArgList(w.var('mjj')),rt.RooArgList(w.var('p0')))
    int_b = background.Integral(w.var('mjj').getMin(),w.var('mjj').getMax())
    p0_b = w.var('Ntot_bkg').getVal() / (int_b * lumi)
    background.SetParameter(0,p0_b)
    
    g_data = rt.TGraphAsymmErrors(myRebinnedTH1)
    
    alpha = 1-0.6827
    for i in range(0,g_data.GetN()):
        N = g_data.GetY()[i]
        binWidth = g_data.GetEXlow()[i] + g_data.GetEXhigh()[i]
        L = 0
        if N!=0:
            L = rt.Math.gamma_quantile(alpha/2,N,1.)
        U = rt.Math.gamma_quantile_c(alpha/2,N+1,1)

        #g_data.SetPointEYlow(i, (N-L))
        #g_data.SetPointEYhigh(i, (U-N))
        #g_data.SetPoint(i, g_data.GetX()[i], N)
        g_data.SetPointEYlow(i, (N-L)/(binWidth * lumi))
        g_data.SetPointEYhigh(i, (U-N)/(binWidth * lumi))
        g_data.SetPoint(i, g_data.GetX()[i], N/(binWidth * lumi))
       

        plotRegions = plotRegion.split(',')
        checkInRegions = [g_data.GetX()[i]>w.var('mjj').getMin(reg) and g_data.GetX()[i]<w.var('mjj').getMax(reg) for reg in plotRegions]
        if not any(checkInRegions):
            g_data.SetPointEYlow(i, 0)
            g_data.SetPointEYhigh(i, 0)
            g_data.SetPoint(i, g_data.GetX()[i], 0)

            
    h_background = convertFunctionToHisto(background,"h_background",len(x)-1,x)
    h_fit_residual_vs_mass = rt.TH1D("h_fit_residual_vs_mass","h_fit_residual_vs_mass",len(x)-1,x)
    list_chi2AndNdf_background = calculateChi2AndFillResiduals(g_data,h_background,h_fit_residual_vs_mass,w,0)

    g_data.SetMarkerStyle(20)
    g_data.SetMarkerSize(0.9)
    g_data.SetLineColor(rt.kBlack)
    g_data_clone = g_data.Clone('g_data_clone')
    g_data_clone.SetMarkerSize(0)
    myRebinnedTH1.SetLineColor(rt.kWhite)
    myRebinnedTH1.SetMarkerSize(0)


    c.Divide(1,2,0,0,0)
    
    pad_1 = c.GetPad(1)
    pad_1.SetPad(0.01,0.36,0.99,0.98)
    pad_1.SetLogy()
    pad_1.SetRightMargin(0.05)
    pad_1.SetTopMargin(0.05)
    pad_1.SetLeftMargin(0.175)
    pad_1.SetFillColor(0)
    pad_1.SetBorderMode(0)
    pad_1.SetFrameFillStyle(0)
    pad_1.SetFrameBorderMode(0)
    
    pad_2 = c.GetPad(2)
    pad_2.SetLeftMargin(0.175)
    pad_2.SetPad(0.01,0.02,0.99,0.37)
    pad_2.SetBottomMargin(0.35)
    pad_2.SetRightMargin(0.05)
    pad_2.SetGridx()
    pad_2.SetGridy()

    pad_1.cd()
    
    myRebinnedDensityTH1 = myRebinnedTH1.Clone('data_obs_density')
    for i in range(1,nBins+1):
        myRebinnedDensityTH1.SetBinContent(i, myRebinnedTH1.GetBinContent(i)/ myRebinnedTH1.GetBinWidth(i))
        myRebinnedDensityTH1.SetBinError(i, myRebinnedTH1.GetBinError(i)/ myRebinnedTH1.GetBinWidth(i))
        
        plotRegions = plotRegion.split(',')
        checkInRegions = [myRebinnedDensityTH1.GetXaxis().GetBinCenter(i)>w.var('mjj').getMin(reg) and myRebinnedDensityTH1.GetXaxis().GetBinCenter(i)<w.var('mjj').getMax(reg) for reg in plotRegions]      
        if not any(checkInRegions):
            myRebinnedDensityTH1.SetBinContent(i,0)
            myRebinnedDensityTH1.SetBinError(i,0)
    myRebinnedDensityTH1.GetXaxis().SetRangeUser(w.var('mjj').getMin(),w.var('mjj').getMax())
    myRebinnedDensityTH1.GetYaxis().SetTitle('d#sigma / dm_{jj} [pb / GeV]')
    myRebinnedDensityTH1.GetYaxis().SetTitleOffset(1)
    myRebinnedDensityTH1.GetYaxis().SetTitleSize(0.07)
    myRebinnedDensityTH1.GetYaxis().SetLabelSize(0.05)
    
    myRebinnedDensityTH1.SetLineColor(rt.kWhite)
    myRebinnedDensityTH1.SetMarkerColor(rt.kWhite)
    myRebinnedDensityTH1.SetLineWidth(0)
    #myRebinnedDensityTH1.SetMaximum(1e3)
    myRebinnedDensityTH1.SetMaximum(2e3)
    myRebinnedDensityTH1.SetMinimum(2e-5)
    #myRebinnedDensityTH1.SetMinimum(2e-10)
    myRebinnedDensityTH1.Draw("pe")    
    g_data_clone.Draw("pezsame")
    background.Draw("csame")
    g_data.Draw("pezsame")

    if options.signalFileName is not None:
        sigHist = signalHistosRebin[0]
        
        g_signal = rt.TGraphAsymmErrors(sigHist)
        g_signal.SetLineColor(rt.kBlue)
        g_signal.SetLineWidth(3)
        g_signal.SetLineStyle(2)

        lastX = 0
        lastY = 0
        for i in range(0,g_signal.GetN()):
            N = g_signal.GetY()[i]
            binWidth = g_signal.GetEXlow()[i] + g_signal.GetEXhigh()[i]
            g_signal.SetPoint(i, g_signal.GetX()[i], N/(binWidth * lumi))
            g_signal.SetPointEYlow(i, 0)
            g_signal.SetPointEYhigh(i, 0)
            if g_signal.GetX()[i]>options.mass*1.5:
                g_signal.SetPoint(i,lastX,lastY)
            else:                
                lastX = g_signal.GetX()[i]
                lastY = g_signal.GetY()[i]
        #sys.exit()
        g_signal.Draw("lxsame")

    
    rt.gPad.SetLogy()
    
    l = rt.TLatex()
    l.SetTextAlign(11)
    l.SetTextSize(0.05)
    l.SetTextFont(42)
    l.SetNDC()
    l.DrawLatex(0.7,0.96,"%i pb^{-1} (%i TeV)"%(lumi,w.var('sqrts').getVal()/1000.))
    l.SetTextFont(62)
    l.SetTextSize(0.06)
    l.DrawLatex(0.2,0.96,"CMS")
    l.SetTextFont(52)
    l.SetTextSize(0.05)
    l.DrawLatex(0.3,0.96,"Preliminary")
    if options.signalFileName!=None:
        leg = rt.TLegend(0.6,0.52,0.89,0.88)
    else:        
        leg = rt.TLegend(0.7,0.7,0.89,0.88)
    leg.SetTextFont(42)
    leg.SetFillColor(rt.kWhite)
    leg.SetFillStyle(0)
    leg.SetLineWidth(0)
    leg.SetLineColor(rt.kWhite)
    leg.AddEntry(g_data,"Data","pe")
    leg.AddEntry(background,"Fit","l")
    if options.signalFileName!=None:
        leg.AddEntry(g_signal,"%s (%i GeV)"%(options.model,options.mass),"l")
        leg.AddEntry(None,"%.1f pb"%(options.xsec),"")
    leg.Draw()
    
    pave_sel = rt.TPaveText(0.2,0.03,0.5,0.25,"NDC")
    pave_sel.SetFillColor(0)
    pave_sel.SetBorderSize(0)
    pave_sel.SetFillStyle(0)
    pave_sel.SetTextFont(42)
    pave_sel.SetTextSize(0.045)
    pave_sel.SetTextAlign(11)
    pave_sel.AddText("#chi^{{2}} / ndf = {0:.1f} / {1:d} = {2:.1f}".format(
                          list_chi2AndNdf_background[4], list_chi2AndNdf_background[5],
                          list_chi2AndNdf_background[4]/list_chi2AndNdf_background[5]))
    pave_sel.AddText("Wide Jets")
    pave_sel.AddText("%i < m_{jj} < %i GeV"%(w.var('mjj').getMin(),w.var('mjj').getMax()))
    pave_sel.AddText("|#eta| < 2.5, |#Delta#eta| < 1.3")
    pave_sel.Draw("SAME")
    
    list_parameter = [p0_b, p0_b*(w.var('Ntot_bkg').getErrorHi() - w.var('Ntot_bkg').getErrorLo())/(2.0*w.var('Ntot_bkg').getVal()),                      
                      w.var('p1').getVal(), (w.var('p1').getErrorHi() - w.var('p1').getErrorLo())/2.0,
                      w.var('p2').getVal(), (w.var('p2').getErrorHi() - w.var('p2').getErrorLo())/2.0,
                      w.var('p3').getVal(), (w.var('p3').getErrorHi() - w.var('p3').getErrorLo())/2.0,
                      w.var('meff').getVal(), (w.var('meff').getErrorHi() - w.var('meff').getErrorLo())/2.0,
                      w.var('seff').getVal(), (w.var('seff').getErrorHi() - w.var('seff').getErrorLo())/2.0]


    pave_param = rt.TPaveText(0.55,0.03,0.9,0.25,"NDC")
    pave_param.SetTextFont(42)
    pave_param.SetFillColor(0)
    pave_param.SetBorderSize(0)
    pave_param.SetFillStyle(0)
    pave_param.SetTextAlign(11)
    pave_param.SetTextSize(0.045)
    pave_param.AddText("p_{0}"+" = {0:.2g} #pm {1:.2g}".format(list_parameter[0], list_parameter[1]))
    pave_param.AddText("p_{1}"+" = {0:.2f} #pm {1:.2f}".format(list_parameter[2], list_parameter[3]))
    pave_param.AddText("p_{2}"+" = {0:.2f} #pm {1:.2f}".format(list_parameter[4], list_parameter[5]))
    pave_param.AddText("p_{3}"+" = {0:.2f} #pm {1:.2f}".format(list_parameter[6], list_parameter[7]))
    if w.var('meff').getVal()>0 and w.var('seff').getVal()>0:
        pave_param.AddText("m_{eff}"+" = {0:.2f} #pm {1:.2f}".format(list_parameter[8], list_parameter[9]))
        pave_param.AddText("#sigma_{eff}"+" = {0:.2f} #pm {1:.2f}".format(list_parameter[10], list_parameter[11]))
    pave_param.Draw("SAME")    
    
    pad_1.Update()

    pad_2.cd()
    
    h_fit_residual_vs_mass.GetXaxis().SetRangeUser(w.var('mjj').getMin(),w.var('mjj').getMax())
    h_fit_residual_vs_mass.GetYaxis().SetRangeUser(-3.5,3.5)
    h_fit_residual_vs_mass.GetYaxis().SetNdivisions(210,True)
    h_fit_residual_vs_mass.SetLineWidth(1)
    h_fit_residual_vs_mass.SetFillColor(rt.kRed)
    h_fit_residual_vs_mass.SetLineColor(rt.kBlack)
    
    h_fit_residual_vs_mass.GetYaxis().SetTitleSize(2*0.06)
    h_fit_residual_vs_mass.GetYaxis().SetLabelSize(2*0.05)
    h_fit_residual_vs_mass.GetYaxis().SetTitleOffset(0.5)
    h_fit_residual_vs_mass.GetYaxis().SetTitle('#frac{(Data-Fit)}{#sigma_{Data}}')
        
    h_fit_residual_vs_mass.GetXaxis().SetTitleSize(2*0.06)
    h_fit_residual_vs_mass.GetXaxis().SetLabelSize(2*0.05)
    h_fit_residual_vs_mass.GetXaxis().SetTitle('m_{jj} [GeV]')
    
    
    h_fit_residual_vs_mass.Draw("histsame")
    
        
    if options.signalFileName is not None:
        sigHistResidual = sigHist.Clone(sigHist.GetName()+"_residual")
        sigHistResidual.SetLineColor(rt.kBlue)
        sigHistResidual.SetLineWidth(2)
        sigHistResidual.SetLineStyle(2)
        for bin in range (0,g_data.GetN()):
            value_data = g_data.GetY()[bin]
            err_tot_data = g_data.GetEYhigh()[bin]
            binWidth = g_data.GetEXlow()[i] + g_data.GetEXhigh()[i]
            value_signal = sigHist.GetBinContent(bin+1)/(binWidth*lumi)
        
            ## Signal residuals
            if err_tot_data>0:                
                sig_residual = (value_signal) / err_tot_data
            else:
                sig_residual = 0                                
    
            ## Fill histo with residuals
            sigHistResidual.SetBinContent(bin+1,sig_residual)
        sigHistResidual.Draw("histsame")

    
    c.Print(options.outDir+"/fit_mjj_%s_%s.pdf"%(fitRegion.replace(',','_'),box))
    c.Print(options.outDir+"/fit_mjj_%s_%s.C"%(fitRegion.replace(',','_'),box))
    tdirectory.cd()
    c.Write()
    

    outFileName = "DijetFitResults_%s.root"%(box)
    outFile = rt.TFile.Open(options.outDir+"/"+outFileName,'recreate')
    outFile.cd()
    w.Write()
    outFile.Close()
