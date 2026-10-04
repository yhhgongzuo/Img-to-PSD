// Native recursive Photoshop composer. Input: validated scene spec from build_psd.ps1.
(function () {
    var oldUnits = app.preferences.rulerUnits, oldDialogs = app.displayDialogs;
    var doc = null, reopened = null, previous = app.documents.length ? app.activeDocument : null;
    function s(v) { return stringIDToTypeID(v); }
    function c(v) { return charIDToTypeID(v); }
    function find(parent,name){
        for(var k=0;k<parent.layers.length;k++){
            var l=parent.layers[k];if(l.name==name)return l;
            if(l.typename=='LayerSet'){var v=find(l,name);if(v)return v;}
        }return null;
    }
    function rgb(hex) { var v = new SolidColor(); v.rgb.hexValue = hex; return v; }
    function ownCopy(path) {
        var ids = [], i;
        for (i=0;i<app.documents.length;i++) ids.push(app.documents[i].id);
        var opened = app.open(File(path)), existed = false;
        for (i=0;i<ids.length;i++) if (ids[i] == opened.id) existed = true;
        var copy = opened.duplicate('layered-psd-source-copy');
        if (!existed) opened.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = copy;
        return copy;
    }
    function imageLayer(item, group) {
        var source = ownCopy(item.path), layer;
        try {
            if(source.width.as('px')!=spec.width || source.height.as('px')!=spec.height)throw Error('Source must retain full scene canvas: '+item.path);
            if(item.frame)throw Error('Frame fitting disabled; use registered full-canvas asset');
            if (item.sourceLayer) source.activeLayer = source.layers.getByName(item.sourceLayer);
            else if (source.layers.length != 1) throw Error('Multilayer source needs sourceLayer: '+item.path);
            if (source.activeLayer.typename != 'ArtLayer') throw Error('Select one source ArtLayer.');
            if (source.activeLayer.isBackgroundLayer) source.activeLayer.isBackgroundLayer = false;
            if (source.activeLayer.kind != LayerKind.SMARTOBJECT) executeAction(s('newPlacedLayer'),undefined,DialogModes.NO);
            layer = source.activeLayer.duplicate(doc,ElementPlacement.PLACEATBEGINNING);
        } finally { source.close(SaveOptions.DONOTSAVECHANGES); }
        app.activeDocument = doc; layer.move(group,ElementPlacement.INSIDE); doc.activeLayer = layer;
        return layer;
    }
    function finishLayer(item,layer){
        layer.name=item.name;doc.activeLayer=layer;
        if(item.postBlur)layer.applyGaussianBlur(item.postBlur);
        if(item.brightness!==undefined || item.contrast!==undefined){
            var adj=new ActionDescriptor();adj.putInteger(c('Brgh'),item.brightness||0);
            adj.putInteger(c('Cntr'),item.contrast||0);adj.putBoolean(s('useLegacy'),false);
            executeAction(c('BrgC'),adj,DialogModes.NO);
        }
        if(item.rgbOffsets){
            var d=new ActionDescriptor(),list=new ActionList(),names=['Rd  ','Grn ','Bl  '],xs=[0,32,64,96,128,160,192,224,255];
            for(var cc=0;cc<3;cc++){
                var curve=new ActionDescriptor(),ref=new ActionReference(),points=new ActionList();
                ref.putEnumerated(c('Chnl'),c('Chnl'),c(names[cc]));curve.putReference(c('Chnl'),ref);
                for(var p=0;p<xs.length;p++){
                    var point=new ActionDescriptor();point.putDouble(c('Hrzn'),xs[p]);
                    point.putDouble(c('Vrtc'),Math.max(0,Math.min(255,xs[p]+item.rgbOffsets[cc])));points.putObject(c('Pnt '),point);
                }
                curve.putList(c('Crv '),points);list.putObject(c('CrvA'),curve);
            }
            d.putList(c('Adjs'),list);executeAction(c('Crvs'),d,DialogModes.NO);
        }
        if(item.hueAdjustments)for(var i=0;i<item.hueAdjustments.length;i++){
            var a=item.hueAdjustments[i],d=new ActionDescriptor(),list=new ActionList(),adj=new ActionDescriptor();
            d.putBoolean(c('Clrz'),false);adj.putInteger(s('localRange'),a.channel);
            adj.putInteger(c('BgnR'),a.range[0]);adj.putInteger(c('BgnS'),a.range[1]);
            adj.putInteger(c('EndS'),a.range[2]);adj.putInteger(c('EndR'),a.range[3]);
            adj.putInteger(c('H   '),a.hue||0);adj.putInteger(c('Strt'),a.saturation||0);adj.putInteger(c('Lght'),a.lightness||0);
            list.putObject(c('Hst2'),adj);d.putList(c('Adjs'),list);executeAction(c('HStr'),d,DialogModes.NO);
        }
        if(item.clipped)layer.grouped=true;
        if(item.opacity!==undefined)layer.opacity=item.opacity;
        if(item.blend)layer.blendMode=BlendMode[item.blend];
        if(item.visible===false)layer.visible=false;
    }
    function fillLayer(item,group) {
        var a=new ActionDescriptor(),r=new ActionReference();r.putClass(s('contentLayer'));a.putReference(c('null'),r);
        var use=new ActionDescriptor(),type=new ActionDescriptor(),col=new ActionDescriptor(),v=rgb(item.color);
        col.putDouble(c('Rd  '),v.rgb.red);col.putDouble(c('Grn '),v.rgb.green);col.putDouble(c('Bl  '),v.rgb.blue);
        type.putObject(c('Clr '),c('RGBC'),col);use.putObject(c('Type'),s('solidColorLayer'),type);
        if(item.box) {
            var sh=new ActionDescriptor(),b=item.box;
            sh.putUnitDouble(c('Left'),c('#Pxl'),b[0]);sh.putUnitDouble(c('Top '),c('#Pxl'),b[1]);
            sh.putUnitDouble(c('Rght'),c('#Pxl'),b[2]);sh.putUnitDouble(c('Btom'),c('#Pxl'),b[3]);
            use.putObject(c('Shp '),item.shape=='ellipse'?c('Elps'):c('Rctn'),sh);
        }
        a.putObject(c('Usng'),s('contentLayer'),use);executeAction(c('Mk  '),a,DialogModes.NO);
        var layer=doc.activeLayer;layer.move(group,ElementPlacement.INSIDE);
        if(item.blur) { executeAction(s('newPlacedLayer'),undefined,DialogModes.NO);doc.activeLayer.applyGaussianBlur(item.blur);layer=doc.activeLayer; }
        return layer;
    }
    function textLayer(item,group) {
        app.fonts.getByName(item.font);
        var layer=group.artLayers.add();layer.kind=LayerKind.TEXT;var t=layer.textItem;
        t.contents=item.text;t.font=item.font;t.size=UnitValue(item.size*72/(spec.resolution||72),'pt');
        t.position=[UnitValue(item.x,'px'),UnitValue(item.y,'px')];t.color=rgb(item.color);t.tracking=item.tracking||0;
        t.antiAliasMethod=AntiAlias.SHARP;
        if(item.align=='right')t.justification=Justification.RIGHT;
        if(item.align=='center')t.justification=Justification.CENTER;
        if(item.fitBox){var b=layer.bounds,f=item.fitBox;layer.resize((f[2]-f[0])/(b[2].as('px')-b[0].as('px'))*100,(f[3]-f[1])/(b[3].as('px')-b[1].as('px'))*100,AnchorPosition.MIDDLECENTER);b=layer.bounds;layer.translate(UnitValue(f[0]-b[0].as('px'),'px'),UnitValue(f[1]-b[1].as('px'),'px'));}
        return layer;
    }
    function verifyNative(group) {
        for(var i=0;i<group.layers.length;i++) {
            var layer=group.layers[i];
            if(layer.typename=='LayerSet') { var visible=layer.visible;layer.visible=!visible;layer.visible=visible;verifyNative(layer); }
            else if(layer.kind==LayerKind.TEXT) { var original=layer.textItem.contents;layer.textItem.contents=original+' ';layer.textItem.contents=original; }
        }
    }
    try {
        app.displayDialogs=DialogModes.NO;app.preferences.rulerUnits=Units.PIXELS;
        doc=spec.basePsd ? ownCopy(spec.basePsd) : app.documents.add(spec.width,spec.height,spec.resolution||72,spec.name||'Layered reconstruction',NewDocumentMode.RGB,DocumentFill.TRANSPARENT,1,BitsPerChannelType.EIGHT,'sRGB IEC61966-2.1');
        if(doc.width.as('px')!=spec.width || doc.height.as('px')!=spec.height)throw Error('Base PSD canvas mismatch');
        var empty=spec.basePsd ? null : doc.activeLayer;
        function buildGroup(conf,parent){
            var group=parent.layerSets.add();group.name=conf.name;
            if(conf.layers)for(var i=0;i<conf.layers.length;i++){
                var item=conf.layers[i],layer;
                if(item.type=='image')layer=imageLayer(item,group);
                else if(item.type=='fill')layer=fillLayer(item,group);
                else if(item.type=='text')layer=textLayer(item,group);
                else throw Error('Unknown layer type');
                finishLayer(item,layer);
            }
            if(conf.groups)for(var j=0;j<conf.groups.length;j++)buildGroup(conf.groups[j],group);
            if(conf.visible===false)group.visible=false;
        }
        function patchGroups(groups){
            for(var g=0;g<groups.length;g++){
                var conf=groups[g];
                if(conf.layers)for(var i=0;i<conf.layers.length;i++){
                    var item=conf.layers[i];if(!item.patch)continue;
                    var old=find(doc,item.name);
                    if(!old || old.typename!='ArtLayer' || old.parent.name!=conf.name)throw Error('Patch target/group missing: '+item.name);
                    var layer=imageLayer(item,old.parent);layer.move(old,ElementPlacement.PLACEBEFORE);old.remove();finishLayer(item,layer);
                }
                if(conf.groups)patchGroups(conf.groups);
            }
        }
        if(spec.basePsd)patchGroups(spec.groups);
        else {for(var g=0;g<spec.groups.length;g++)buildGroup(spec.groups[g],doc);empty.remove();}
        doc.info.title=spec.name||'Layered reconstruction';doc.info.caption=spec.caption||'';
        app.refresh();
        var psd=new PhotoshopSaveOptions();psd.layers=true;psd.embedColorProfile=true;psd.maximizeCompatibility=true;
        doc.saveAs(File(spec.outputPsd),psd,true);
        var jpg=new JPEGSaveOptions();jpg.quality=12;jpg.embedColorProfile=true;
        doc.saveAs(File(spec.outputJpg),jpg,true);
        doc.close(SaveOptions.DONOTSAVECHANGES);doc=null;
        reopened=app.open(File(spec.outputPsd));verifyNative(reopened);app.refresh();
        if(spec.qaMoveGroup){
            var moving=find(reopened,spec.qaMoveGroup);
            if(!moving || moving.typename!='LayerSet')throw Error('Missing move test group');
            // Duplicated smart-filter layers may initially report the full canvas as bounds.
            // A reversible integer translation materializes their actual alpha bounds.
            moving.translate(UnitValue(1,'px'),UnitValue(0,'px'));
            moving.translate(UnitValue(-1,'px'),UnitValue(0,'px'));
            var before=[];
            for(var q=0;q<moving.layers.length;q++)before.push([moving.layers[q].bounds[0].as('px'),moving.layers[q].bounds[1].as('px')]);
            moving.translate(UnitValue(17,'px'),UnitValue(9,'px'));
            for(var q=0;q<moving.layers.length;q++)if(Math.abs(moving.layers[q].bounds[0].as('px')-before[q][0]-17)>1 || Math.abs(moving.layers[q].bounds[1].as('px')-before[q][1]-9)>1)throw Error('Child did not move with group');
            moving.translate(UnitValue(-17,'px'),UnitValue(-9,'px'));
            for(var q=0;q<moving.layers.length;q++)if(Math.abs(moving.layers[q].bounds[0].as('px')-before[q][0])>1 || Math.abs(moving.layers[q].bounds[1].as('px')-before[q][1])>1)throw Error('Group restore failed');
        }
        reopened.saveAs(File(spec.qaPng),new PNGSaveOptions(),true);
        if(spec.qaViews)for(var v=0;v<spec.qaViews.length;v++){
            var conf=spec.qaViews[v],state=[];
            function remember(p){for(var k=0;k<p.layers.length;k++){var l=p.layers[k];state.push([l,l.visible]);if(l.typename=='LayerSet')remember(l);}}
            remember(reopened);
            if(conf.onlyRoot)for(var k=0;k<reopened.layers.length;k++)reopened.layers[k].visible=reopened.layers[k].name==conf.onlyRoot;
            if(conf.hide)for(var k=0;k<conf.hide.length;k++){var l=find(reopened,conf.hide[k]);if(!l)throw Error('Unknown QA hide target');l.visible=false;}
            reopened.saveAs(File(conf.path),new PNGSaveOptions(),true);
            for(var k=state.length-1;k>=0;k--)state[k][0].visible=state[k][1];
        }
        // Export final filtered objects at their original canvas positions, without trim.
        var exportState=[];
        function collectState(p){for(var k=0;k<p.layers.length;k++){var l=p.layers[k];exportState.push([l,l.visible]);if(l.typename=='LayerSet')collectState(l);}}
        collectState(reopened);
        function exportGroups(groups){
            for(var g=0;g<groups.length;g++){
                var conf=groups[g];
                if(conf.layers)for(var i=0;i<conf.layers.length;i++){
                    var item=conf.layers[i];if(!item.exportPng)continue;
                    for(var j=0;j<exportState.length;j++)exportState[j][0].visible=false;
                    var target=find(reopened,item.name);target.visible=true;
                    for(var parent=target.parent;parent.typename=='LayerSet';parent=parent.parent)parent.visible=true;
                    reopened.saveAs(File(item.exportPng),new PNGSaveOptions(),true);
                }
                if(conf.groups)exportGroups(conf.groups);
            }
        }
        exportGroups(spec.groups);
        for(var k=exportState.length-1;k>=0;k--)exportState[k][0].visible=exportState[k][1];
        reopened.close(SaveOptions.DONOTSAVECHANGES);reopened=null;
        return 'Saved PSD/JPG; reopened and exercised native text and group visibility; QA PNG exported.';
    } finally {
        if(doc)doc.close(SaveOptions.DONOTSAVECHANGES);
        if(reopened)reopened.close(SaveOptions.DONOTSAVECHANGES);
        app.preferences.rulerUnits=oldUnits;app.displayDialogs=oldDialogs;
        if(previous)try{app.activeDocument=previous;}catch(ignored){}
    }
}());
